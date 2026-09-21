"""Python metrics engine.

Nguyen tac cot loi cua ca he thong: **Python tinh toan, LLM chi dien giai**.
Moi con so (KPI, baseline, muc canh bao) deu tinh bang pandas/numpy o day -
LLM (app/llm_insight.py) khong duoc tu tinh lai bat ky con so nao, chi duoc
doc ket qua da tinh san va giai thich bang loi.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROLLING_WINDOW_DAYS = 7
TREND_DAYS = 3

# Ten KPI (tieng Viet) de hien thi tren dashboard / dua vao prompt LLM
# Luu y: schema du lieu that khong co buoc "Quan tam" rieng (giua Tiep can
# va Dang ky). Chi phi (ad_spend) lay tu bang fact_lead_cost - xem ghi chu
# trong app/data_source.py::aggregate_real_schema().
KPI_LABELS = {
    "cpl": "Chi phí mỗi lead (CPL)",
    "cpa": "Chi phí mỗi khoản vay giải ngân (CPA, xấp xỉ)",
    "lead_to_registration_rate": "Tỷ lệ Lead → Đăng ký",
    "approval_rate": "Tỷ lệ duyệt đơn (Đăng ký → Duyệt)",
    "roi_outstanding": "Thu nhập lãi trên dư nợ",
    "early_settlement_rate": "Tỷ lệ tất toán sớm",
    "reach_count": "Số lượng lead tiếp cận",
    "registered_count": "Số hồ sơ đăng ký",
    "approved_count": "Số đơn được duyệt",
    "disbursed_count": "Số đơn giải ngân",
    "disbursed_amount": "Doanh số giải ngân",
}

# Huong canh bao cho tung loai KPI (theo Muc 4 cua plan):
#   "cost"       -> canh bao khi TANG bat thuong
#   "conversion" -> canh bao khi GIAM bat thuong
#   "volume"     -> canh bao ca hai chieu (tang hoac giam bat thuong deu dang chu y)
KPI_DIRECTIONS = {
    "cpl": "cost",
    "cpa": "cost",
    "lead_to_registration_rate": "conversion",
    "approval_rate": "conversion",
    # KHONG theo doi "disbursement_rate" (Duyet -> Giai ngan) o day: don da
    # duyet gan nhu chac chan se giai ngan, chi lech do do tre xu ly cohort
    # (xem note trong dropoff_approve_to_disburse) nen khong phai tin hieu
    # kinh doanh that su - de trong se lam ra canh bao/nhan dinh gia (false
    # positive) theo yeu cau nguoi dung.
    "roi_outstanding": "conversion",
    "early_settlement_rate": "volume",
    "reach_count": "volume",
    "registered_count": "volume",
    "approved_count": "volume",
    "disbursed_count": "volume",
    "disbursed_amount": "volume",
}

FUNNEL_STEPS = [
    ("reach_count", "1. Tiếp cận"),
    ("registered_count", "2. Đăng ký"),
    ("approved_count", "3. Duyệt đơn"),
    ("disbursed_count", "4. Giải ngân"),
    ("settled_count", "5. Tất toán"),
]


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator.replace(0, np.nan)
    return result


def compute_daily_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """Them cac cot KPI da tinh vao tung dong (moi dong = 1 campaign/1 ngay)."""
    out = df.copy()

    out["cpl"] = _safe_div(out["ad_spend"], out["reach_count"])
    out["lead_to_registration_rate"] = _safe_div(out["registered_count"], out["reach_count"])
    out["approval_rate"] = _safe_div(out["approved_count"], out["registered_count"])
    out["disbursement_rate"] = _safe_div(out["disbursed_count"], out["approved_count"])
    out["cpa"] = _safe_div(out["ad_spend"], out["disbursed_count"])
    # CAC: chi phi lead + marketing cua cac ho so DA GIAI NGAN / so ho so da giai ngan
    out["cac"] = _safe_div(pd.to_numeric(out["disbursed_acq_cost"], errors="coerce"), out["disbursed_count"])
    out["avg_loan_value"] = _safe_div(out["disbursed_amount"], out["disbursed_count"])
    # Chi phi tren moi dong doanh so giai ngan - luu y: ad_spend tinh theo
    # ngay TAO LEAD con disbursed_amount tinh theo ngay GIAI NGAN (khac nhom
    # khach), nen chi mang tinh tham khao chieu huong cho 1 ngay.
    out["cost_to_disbursed_ratio"] = _safe_div(out["ad_spend"], out["disbursed_amount"])
    out["early_settlement_rate"] = _safe_div(out["early_settled_count"], out["settled_count"])
    out["roi_outstanding"] = _safe_div(out["interest_income"], out["outstanding_balance"])
    out["total_journey_hours"] = out["avg_processing_time_hours"]

    # Ty le rot (drop-off) giua cac buoc funnel lien tiep
    out["dropoff_reach_to_register"] = 1 - out["lead_to_registration_rate"]
    out["dropoff_register_to_approve"] = 1 - out["approval_rate"]
    out["dropoff_approve_to_disburse"] = 1 - out["disbursement_rate"]

    return out


def _classify(z: float, kpi_type: str) -> tuple[str, float]:
    """Tra ve (muc_canh_bao, bad_z). bad_z duong = dang di theo huong xau."""
    if pd.isna(z):
        return "khong_du_du_lieu", 0.0
    if kpi_type == "cost":
        bad_z = z
    elif kpi_type == "conversion":
        bad_z = -z
    else:  # volume: ca hai chieu deu dang chu y
        bad_z = abs(z)

    if bad_z > 2:
        return "critical", bad_z
    if bad_z > 1:
        return "warning", bad_z
    return "normal", bad_z


def detect_anomalies(kpi_df: pd.DataFrame, as_of_date=None) -> pd.DataFrame:
    """So sanh KPI cua ngay `as_of_date` (mac dinh: ngay moi nhat) voi baseline
    dong (trung binh + do lech chuan 7 ngay truoc do) cho tung campaign.

    Tra ve DataFrame dang "long": moi dong la 1 (campaign, kpi) tai as_of_date,
    kem baseline_mean, baseline_std, z_score, level, is_bad_trend_3d.
    """
    df = kpi_df.sort_values(["campaign_name", "date"]).copy()
    if as_of_date is None:
        as_of_date = df["date"].max()

    kpi_cols = list(KPI_DIRECTIONS.keys())
    records = []

    for campaign, g in df.groupby("campaign_name"):
        g = g.sort_values("date").reset_index(drop=True)
        if as_of_date not in set(g["date"]):
            continue
        idx_today = g.index[g["date"] == as_of_date][0]
        product = g.loc[idx_today, "product"] if "product" in g.columns else "Tat ca"

        for kpi in kpi_cols:
            kpi_type = KPI_DIRECTIONS[kpi]
            today_val = g.loc[idx_today, kpi]

            if pd.isna(today_val):
                records.append(dict(
                    date=as_of_date, campaign_name=campaign, product=product, kpi=kpi, kpi_label=KPI_LABELS[kpi],
                    kpi_type=kpi_type, value=today_val, baseline_mean=np.nan, baseline_std=np.nan,
                    z_score=np.nan, bad_z=np.nan, level="khong_ap_dung", bad_trend_3d=False,
                ))
                continue

            history = g.loc[max(0, idx_today - ROLLING_WINDOW_DAYS):idx_today - 1, kpi].dropna()

            if len(history) < 3:
                records.append(dict(
                    date=as_of_date, campaign_name=campaign, product=product, kpi=kpi, kpi_label=KPI_LABELS[kpi],
                    kpi_type=kpi_type, value=today_val, baseline_mean=np.nan, baseline_std=np.nan,
                    z_score=np.nan, bad_z=np.nan, level="chua_du_du_lieu", bad_trend_3d=False,
                ))
                continue

            baseline_mean = history.mean()
            baseline_std = history.std(ddof=0)
            if baseline_std == 0 or pd.isna(baseline_std):
                z = 0.0 if today_val == baseline_mean else (4.0 if today_val > baseline_mean else -4.0)
            else:
                z = (today_val - baseline_mean) / baseline_std

            level, bad_z = _classify(z, kpi_type)

            # Xu huong xau lien tuc >= TREND_DAYS ngay: kiem tra bad_z cua 3 ngay gan nhat (ke ca hom nay) > 1
            bad_trend = False
            if idx_today - (TREND_DAYS - 1) >= 0:
                recent_bad_flags = []
                for offset in range(TREND_DAYS):
                    i = idx_today - offset
                    hist_i = g.loc[max(0, i - ROLLING_WINDOW_DAYS):i - 1, kpi].dropna()
                    if len(hist_i) < 3:
                        recent_bad_flags.append(False)
                        continue
                    m_i, s_i = hist_i.mean(), hist_i.std(ddof=0)
                    if s_i == 0 or pd.isna(s_i):
                        recent_bad_flags.append(False)
                        continue
                    z_i = (g.loc[i, kpi] - m_i) / s_i
                    _, bad_z_i = _classify(z_i, kpi_type)
                    recent_bad_flags.append(bad_z_i > 1)
                bad_trend = all(recent_bad_flags)
                if bad_trend and level != "critical":
                    level = "critical"

            records.append(dict(
                date=as_of_date, campaign_name=campaign, product=product, kpi=kpi, kpi_label=KPI_LABELS[kpi],
                kpi_type=kpi_type, value=today_val, baseline_mean=baseline_mean,
                baseline_std=baseline_std, z_score=z, bad_z=bad_z, level=level, bad_trend_3d=bad_trend,
            ))

    return pd.DataFrame.from_records(records)


def build_funnel_summary(kpi_df: pd.DataFrame, as_of_date=None) -> pd.DataFrame:
    """So luong tung buoc funnel + ty le rot, cho ngay as_of_date, theo tung campaign."""
    df = kpi_df.copy()
    if as_of_date is None:
        as_of_date = df["date"].max()
    today = df[df["date"] == as_of_date].copy()
    cols = [c for c, _ in FUNNEL_STEPS] + [
        "campaign_name", "channel", "product",
        "dropoff_reach_to_register",
        "dropoff_register_to_approve",
        "cpl", "cpa", "cac", "avg_loan_value", "roi_outstanding", "total_journey_hours",
        "ad_spend", "disbursed_amount", "cost_to_disbursed_ratio",
        "avg_reloan_cadence_days",
        "install_count", "phone_verified_count",
        "avg_lead_to_install_hours", "avg_install_to_phone_hours", "avg_phone_to_apply_hours",
    ]
    return today[[c for c in cols if c in today.columns]].reset_index(drop=True)


CONTRIBUTION_METRICS = {
    "disbursed_count": "Số đơn giải ngân",
    "disbursed_amount": "Doanh số giải ngân",
    "registered_count": "Số hồ sơ đăng ký",
    "ad_spend": "Chi phí lead",
}


def compute_channel_contributions(kpi_df: pd.DataFrame, as_of_date=None) -> dict:
    """Dong gop cua TUNG KENH vao muc tang/giam chung cua san pham so voi
    trung binh 7 ngay truoc do: thay_doi cua kenh = hom nay - TB 7 ngay cua
    chinh kenh; ty trong = thay_doi kenh / tong thay_doi (am neu kenh di nguoc
    chieu voi xu huong chung). Tra ve {} neu san pham chi co 1 kenh."""
    df = kpi_df
    if as_of_date is None:
        as_of_date = df["date"].max()
    cols = list(CONTRIBUTION_METRICS)
    prior = sorted(d for d in df["date"].unique() if d < as_of_date)[-ROLLING_WINDOW_DAYS:]
    today = df[df["date"] == as_of_date].groupby("campaign_name")[cols].sum()
    base = df[df["date"].isin(prior)].groupby("campaign_name")[cols].mean()
    if len(today) < 2 or base.empty:
        return {}

    out = {}
    for col, label in CONTRIBUTION_METRICS.items():
        delta = (today[col] - base[col].reindex(today.index)).dropna()
        total_delta = float(delta.sum())
        total_base = float(base[col].sum())
        # Tong bien dong qua nho (<1% so voi TB 7 ngay) thi ty trong khong con y nghia
        share_ok = total_base > 0 and abs(total_delta) >= 0.01 * total_base
        rows = [
            dict(
                campaign_name=ch, hom_nay=float(today.at[ch, col]), tb_7_ngay=float(base.at[ch, col]), thay_doi=float(d),
                ty_trong_pct=round(float(d) / total_delta * 100, 1) if share_ok else None,
            )
            for ch, d in delta.sort_values(key=lambda s: s.abs(), ascending=False).items()
        ]
        out[col] = dict(
            col=col, label=label, tong_hom_nay=float(today[col].sum()), tong_tb_7_ngay=total_base,
            tong_thay_doi=total_delta,
            tong_thay_doi_pct=round(total_delta / total_base * 100, 1) if total_base > 0 else None,
            theo_kenh=rows,
        )
    return out


def get_latest_date(df: pd.DataFrame):
    return df["date"].max()
