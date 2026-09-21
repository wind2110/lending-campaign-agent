import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

# Schema tong hop "1 dong = 1 ngay x 1 kenh" ma app/metrics.py can.
# 5 buoc funnel (khong co "Quan tam" rieng - schema that khong co bang nao
# ghi nhan buoc nay, xem ghi chu trong aggregate_real_schema()).
REQUIRED_COLUMNS = [
    "date", "campaign_name", "channel",
    "ad_spend", "reach_count",
    "registered_count", "approved_count", "rejected_count", "top_reject_reason",
    "avg_processing_time_hours",
    "disbursed_count", "disbursed_amount",
    "settled_count", "early_settled_count",
    "outstanding_balance", "interest_income",
]

# Cot bo sung (thong tin tham khao, khong bat buoc, khong dua vao phat hien
# bat thuong) - chi co gia tri neu file dau vao co du du lieu de tinh.
EXTRA_COLUMNS = [
    "avg_reloan_cadence_days", "disbursed_acq_cost",
    "install_count", "phone_verified_count",
    "avg_lead_to_install_hours", "avg_install_to_phone_hours", "avg_phone_to_apply_hours",
]

DATA_SOURCE_PATH = os.environ.get("DATA_SOURCE_PATH", "data/full_schema_mock.xlsx")

# Nguon du lieu Google Sheets (uu tien hon DATA_SOURCE_PATH khi dat GOOGLE_SHEET_ID): moi lan chay,
# agent tai ban .xlsx cua CA Sheet (tat ca cac tab) ve file cache roi doc nhu file Excel.
GOOGLE_SHEET_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
GOOGLE_SHEET_CACHE_PATH = "data/google_sheet_cache.xlsx"

REAL_SCHEMA_SHEETS = [
    "fact_lead", "fact_loan", "fact_reject", "dim_customer",
    "loan_application_pnl", "fact_digital_footprint",
]


def _is_real_schema_file(path: str) -> bool:
    xl = pd.ExcelFile(path)
    return "fact_lead" in xl.sheet_names and "fact_loan" in xl.sheet_names


def _mode_or_blank(s: pd.Series) -> str:
    s = s.dropna()
    if s.empty:
        return ""
    return s.mode().iloc[0]


def aggregate_real_schema(path: str) -> pd.DataFrame:
    """Tong hop 6 bang chi tiet (fact_lead, fact_loan, fact_reject,
    loan_application_pnl,...) thanh dung schema REQUIRED_COLUMNS ma
    app/metrics.py can - 1 dong = 1 ngay x 1 kenh.

    2 gioi han that cua schema goc, xu ly nhu sau (khong bia so lieu):
    - Khong co bang nao ghi nhan buoc "Quan tam" (giua Tiep can va Dang ky)
      -> chi con 5 buoc funnel: Tiep can -> Dang ky -> Duyet -> Giai ngan -> Tat toan.
    - fact_loan/fact_reject/loan_application_pnl KHONG co campaign_name (chi
      fact_lead co) -> nhom theo "channel" (kenh) thay vi tung campaign rieng,
      vi day la muc chi tiet toi da ma du lieu that cho phep xuyen suot funnel.
    - Chi phi lead (ad_spend) lay tu sheet fact_lead_cost (chi phi thuc te theo
      ngay x chien dich), KHONG con xap xi tu chi phi cua cac ho so dang ky.
      CPL = chi phi lead trong ngay / so lead tiep can cung ngay, cung kenh.
    - outstanding_balance / interest_income tinh tren nhom KHOAN VAY GIAI
      NGAN trong ngay do (khong phai snapshot du no toan bo portfolio moi ngay,
      vi du lieu goc khong luu chuoi du no theo thoi gian).
    """
    sheets = pd.read_excel(path, sheet_name=None)
    lead = sheets["fact_lead"].copy()
    loan = sheets["fact_loan"].copy()
    reject = sheets["fact_reject"].copy()
    pnl = sheets["loan_application_pnl"].copy()

    lead["date"] = pd.to_datetime(lead["create_at"]).dt.date
    loan["reg_date"] = pd.to_datetime(loan["create_at"]).dt.date
    loan["disb_date"] = pd.to_datetime(loan["disbursement_date"]).dt.date
    loan["settle_date"] = pd.to_datetime(loan["settlement_date"]).dt.date

    all_dates = sorted(set(lead["date"]) | set(loan["reg_date"].dropna()))
    all_channels = sorted(set(lead["channel"].dropna()) | set(loan["channel"].dropna()))
    grid = pd.MultiIndex.from_product([all_dates, all_channels], names=["date", "channel"]).to_frame(index=False)

    # 1. Tiep can (reach)
    reach = lead.groupby(["date", "channel"]).size().reset_index(name="reach_count")

    # 2. Dang ky (registered) - tat ca don tao trong ngay
    registered = loan.groupby(["reg_date", "channel"]).size().reset_index(name="registered_count")
    registered = registered.rename(columns={"reg_date": "date"})

    rejected_ids = set(reject["loan_application_id"])
    loan["is_rejected"] = loan["application_id"].isin(rejected_ids)

    approved = (loan[~loan["is_rejected"]].groupby(["reg_date", "channel"]).size()
                .reset_index(name="approved_count").rename(columns={"reg_date": "date"}))
    rejected_cnt = (loan[loan["is_rejected"]].groupby(["reg_date", "channel"]).size()
                    .reset_index(name="rejected_count").rename(columns={"reg_date": "date"}))

    reject_with_ctx = reject.merge(
        loan[["application_id", "reg_date", "channel"]],
        left_on="loan_application_id", right_on="application_id", how="left",
    )
    top_reason = (reject_with_ctx.groupby(["reg_date", "channel"])["reason_level_1"]
                  .apply(_mode_or_blank).reset_index(name="top_reject_reason")
                  .rename(columns={"reg_date": "date"}))

    # 3. Giai ngan (disbursed)
    disbursed_loans = loan[loan["disb_date"].notna()].copy()
    disbursed = (disbursed_loans.groupby(["disb_date", "channel"])
                 .agg(disbursed_count=("application_id", "size"), disbursed_amount=("loan_amount", "sum"))
                 .reset_index().rename(columns={"disb_date": "date"}))

    disbursed_loans["processing_hours"] = (
        pd.to_datetime(disbursed_loans["disbursement_date"]) - pd.to_datetime(disbursed_loans["create_at"])
    ).dt.total_seconds() / 3600
    avg_processing = (disbursed_loans.groupby(["disb_date", "channel"])["processing_hours"].mean()
                       .reset_index(name="avg_processing_time_hours").rename(columns={"disb_date": "date"}))

    disb_with_pnl = disbursed_loans.merge(
        pnl[["loan_application_id", "interest_income", "lead_cost", "marketing_cost"]],
        left_on="application_id", right_on="loan_application_id", how="left",
    )
    # CAC chi tinh tren cac ho so DA GIAI NGAN: chi phi lead + marketing cua chinh cac ho so do
    disb_with_pnl["acq_cost"] = disb_with_pnl["lead_cost"].fillna(0) + disb_with_pnl["marketing_cost"].fillna(0)
    balance_income = (disb_with_pnl.groupby(["disb_date", "channel"])
                       .agg(outstanding_balance=("loan_balance", "sum"), interest_income=("interest_income", "sum"),
                            disbursed_acq_cost=("acq_cost", "sum"))
                       .reset_index().rename(columns={"disb_date": "date"}))

    # 4. Tat toan (settled)
    settled_loans = loan[loan["settle_date"].notna()].merge(
        pnl[["loan_application_id", "early_paid_off_fee"]],
        left_on="application_id", right_on="loan_application_id", how="left",
    )
    settled = (settled_loans.groupby(["settle_date", "channel"]).size()
               .reset_index(name="settled_count").rename(columns={"settle_date": "date"}))
    early_settled = (settled_loans[settled_loans["early_paid_off_fee"] > 0]
                      .groupby(["settle_date", "channel"]).size()
                      .reset_index(name="early_settled_count").rename(columns={"settle_date": "date"}))

    # 5. Chi phi lead thuc te theo ngay x kenh (sheet fact_lead_cost)
    if "fact_lead_cost" not in sheets:
        raise ValueError("Thieu sheet 'fact_lead_cost' (chi phi lead theo ngay) - CPL/CPA can bang nay.")
    lead_cost = sheets["fact_lead_cost"].copy()
    lead_cost["date"] = pd.to_datetime(lead_cost["cost_date"]).dt.date
    ad_spend = lead_cost.groupby(["date", "channel"])["lead_cost"].sum().reset_index(name="ad_spend")

    extra_parts = []

    # 7. Bao lau tai vay (Re-loan) - da tinh san reloan_cadence_days luc mock
    if "reloan_cadence_days" in loan.columns:
        reloan_cadence = (loan.dropna(subset=["reloan_cadence_days"])
                           .groupby(["reg_date", "channel"])["reloan_cadence_days"].mean()
                           .reset_index(name="avg_reloan_cadence_days").rename(columns={"reg_date": "date"}))
        extra_parts.append(reloan_cadence)

    # 8. Hanh trinh sau khi tuong tac quang cao: Lead -> Cai app -> Xac thuc SDT
    # -> Dang ky vay. BANG MOCK BO SUNG (fact_app_install), khong co trong schema
    # goc ngan hang - chi ap dung First Loan (Re-loan da co app/tai khoan).
    # Cai app / xac thuc SDT tinh theo NGAY CAI (khach co the cai sau khi tuong
    # tac quang cao vai gio, nen khong khop 1-1 voi so lead cung ngay).
    if "fact_app_install" in sheets:
        install = sheets["fact_app_install"].copy()
        install["date"] = pd.to_datetime(install["install_time"]).dt.date
        install_count = install.groupby(["date", "channel"]).size().reset_index(name="install_count")
        phone_verified_count = (install[install["phone_verified_time"].notna()]
                                 .groupby(["date", "channel"]).size().reset_index(name="phone_verified_count"))

        def _avg_hours(df, start, end, group_date, name):
            hours = (pd.to_datetime(df[end]) - pd.to_datetime(df[start])).dt.total_seconds() / 3600
            return (df.assign(_h=hours).dropna(subset=["_h"])
                    .groupby([group_date, "channel"])["_h"].mean()
                    .reset_index(name=name).rename(columns={group_date: "date"}))

        avg_install_to_phone = _avg_hours(install, "install_time", "phone_verified_time", "date", "avg_install_to_phone_hours")

        install_with_lead = install.merge(
            lead[["lead_id", "create_at"]].rename(columns={"create_at": "lead_create_at"}), on="lead_id", how="left",
        )
        avg_lead_to_install = _avg_hours(install_with_lead, "lead_create_at", "install_time", "date", "avg_lead_to_install_hours")

        # Noi xac thuc SDT -> khoan vay qua Customer_id (fact_lead duoc bo sung Customer_id
        # sau khi khach xac thuc SDT); lay lan xac thuc gan nhat truoc luc dang ky
        cust_verified = (install.merge(lead[["lead_id", "Customer_id"]].dropna(subset=["Customer_id"]), on="lead_id")
                         .dropna(subset=["phone_verified_time"])[["Customer_id", "phone_verified_time"]])
        verified_then_applied = loan.merge(cust_verified, on="Customer_id", how="inner")
        verified_then_applied = verified_then_applied[verified_then_applied["phone_verified_time"] < verified_then_applied["create_at"]]
        verified_then_applied = verified_then_applied.sort_values("phone_verified_time").groupby("application_id", as_index=False).last()
        avg_phone_to_apply = _avg_hours(verified_then_applied, "phone_verified_time", "create_at", "reg_date", "avg_phone_to_apply_hours")

        extra_parts += [install_count, phone_verified_count, avg_install_to_phone, avg_lead_to_install, avg_phone_to_apply]

    out = grid
    for part in [reach, registered, approved, rejected_cnt, top_reason, disbursed,
                 avg_processing, balance_income, settled, early_settled, ad_spend] + extra_parts:
        out = out.merge(part, on=["date", "channel"], how="left")

    count_cols = ["reach_count", "registered_count", "approved_count", "rejected_count",
                  "disbursed_count", "settled_count", "early_settled_count"]
    for c in count_cols:
        out[c] = out[c].fillna(0).astype(int)
    for c in ["disbursed_amount", "outstanding_balance", "interest_income", "ad_spend", "disbursed_acq_cost"]:
        out[c] = out[c].fillna(0.0)
    for c in ["install_count", "phone_verified_count"]:
        if c in out.columns:
            out[c] = out[c].fillna(0).astype(int)
    out["top_reject_reason"] = out["top_reject_reason"].fillna("")
    # avg_processing_time_hours va cac avg_* khac: giu NaN neu ngay/kenh do
    # khong co du lieu (khong ep ve 0, tranh lam sai lech baseline)
    for c in EXTRA_COLUMNS:
        if c not in out.columns:
            out[c] = pd.NA

    out["campaign_name"] = out["channel"]  # xem ghi chu o docstring: gom theo kenh

    # Gan nhan san pham (First Loan / Re-loan) cho tung kenh - de dashboard loc
    # theo tab. Uu tien lay tu product_name thuc te cua fact_loan (da dang ky);
    # kenh nao chua co don nao thi fallback sang product_id cua fact_lead
    # (san pham duoc TARGET luc tao lead).
    loan_product_mode = loan.groupby("channel")["product_name"].agg(_mode_or_blank)
    lead_product_map = {"PRD-FL": "First Loan", "PRD-RL": "Re-loan"}
    lead_product_mode = lead.groupby("channel")["product_id"].agg(_mode_or_blank).map(lead_product_map)
    channel_product = loan_product_mode.combine_first(lead_product_mode).to_dict()
    out["product"] = out["channel"].map(channel_product).fillna("Khac")

    out = out[REQUIRED_COLUMNS + ["product"] + EXTRA_COLUMNS]
    out = out.sort_values(["campaign_name", "date"]).reset_index(drop=True)
    return out


MAX_SHEET_BYTES = 50 * 1024 * 1024


def download_google_sheet(sheet: str, dest: str = GOOGLE_SHEET_CACHE_PATH) -> str:
    """Tai ban .xlsx cua Google Sheet ve `dest` va tra ve duong dan. `sheet` la ID hoac ca duong link.
    Yeu cau Sheet bat chia se "Bat ky ai co duong lien ket" (quyen Nguoi xem) - khong can tai khoan ky thuat.
    Loi thi bao ro nguyen nhan (khong am tham dung du lieu cu de tranh hien so lieu cu ma khong ai biet)."""
    match = re.search(r"/d/([\w-]+)", sheet)
    sheet_id = match.group(1) if match else sheet.strip()
    # ID Google Sheet chi gom chu/so/_/-. Kiem tra chat de chuoi la trong bien moi truong khong the
    # chen them duong dan/tham so vao URL tai ve.
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", sheet_id):
        raise RuntimeError("GOOGLE_SHEET_ID khong hop le (chi gom chu, so, dau _ va -; hoac dan ca link Google Sheet).")
    request = urllib.request.Request(
        GOOGLE_SHEET_EXPORT_URL.format(sheet_id=sheet_id), headers={"User-Agent": "lending-campaign-agent"},
    )
    try:
        # URL luon la https://docs.google.com/... (mau co dinh + ID da kiem tra o tren)
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
            content = response.read(MAX_SHEET_BYTES + 1)  # gioi han dung luong, tranh tai file khong lo vao RAM
        if len(content) > MAX_SHEET_BYTES:
            raise RuntimeError("File Google Sheet tai ve qua lon (>50MB).")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Khong tai duoc Google Sheet (HTTP {e.code}). Kiem tra GOOGLE_SHEET_ID va da bat chia se "
            "'Bat ky ai co duong lien ket' (quyen Nguoi xem) chua."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Khong ket noi duoc toi Google Sheets: {e.reason}") from e
    if not content.startswith(b"PK"):  # .xlsx la file zip; Sheet chua chia se thi Google tra ve trang dang nhap (HTML)
        raise RuntimeError(
            "Google Sheet khong tra ve file Excel - thuong do chua bat chia se 'Bat ky ai co duong lien ket' "
            "(quyen Nguoi xem), hoac GOOGLE_SHEET_ID sai, hoac file la .xlsx chua doi sang Google Sheets."
        )
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest + ".tmp"
    Path(tmp_path).write_bytes(content)
    os.replace(tmp_path, dest)
    return dest


def load_funnel_history() -> pd.DataFrame:
    """Doc toan bo lich su du lieu funnel (nhieu ngay x nhieu kenh/campaign).

    Nguon: Google Sheet (neu dat GOOGLE_SHEET_ID) hoac file Excel cuc bo (DATA_SOURCE_PATH).

    Tu dong nhan dien 2 dang file dau vao:
    - File tong hop san 1 sheet "funnel_daily" (dung khi da co san so lieu
      tong hop moi ngay, vi du xuat tu 1 he thong BI khac).
    - File schema chi tiet ngan hang that (co sheet "fact_lead", "fact_loan",
      ...) -> tu dong goi aggregate_real_schema() de tong hop lai.

    Khi co database that, chi can thay noi dung ham nay (hoac them 1 nhanh
    moi) tra ve DataFrame cung schema REQUIRED_COLUMNS - phan tinh KPI o
    metrics.py khong can sua gi.
    """
    google_sheet = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    source_path = download_google_sheet(google_sheet) if google_sheet else DATA_SOURCE_PATH

    if not os.path.exists(source_path):
        raise FileNotFoundError(
            f"Khong tim thay file du lieu dau vao: {source_path}. "
            "Xem data/SCHEMA.md de biet cau truc file can co."
        )

    if _is_real_schema_file(source_path):
        df = aggregate_real_schema(source_path)
    else:
        df = pd.read_excel(source_path, sheet_name="funnel_daily")
        if "product" not in df.columns:
            df["product"] = "Tat ca"  # file tong hop san khong phan biet san pham
        for c in EXTRA_COLUMNS:
            if c not in df.columns:
                df[c] = pd.NA

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"File du lieu thieu cac cot bat buoc: {sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values(["campaign_name", "date"]).reset_index(drop=True)
    return df
