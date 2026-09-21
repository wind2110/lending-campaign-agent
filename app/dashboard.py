"""Ghep chart + narrative (tu LLM) thanh 1 file HTML dashboard tu chua.

Bo cuc theo dung gop y sau khi phan bien lai ban dau:
- 1 hop "tom tat nhanh" tren cung (top 3 van de nghiem trong nhat + top 3
  de xuat uu tien cao, gop ca 2 san pham) - de nguoi doc 30s van biet phai
  lam gi, khong phai cuon het trang moi thay "De xuat hanh dong".
- Chip trang thai kenh sap theo MUC DO NGHIEM TRONG (bad_z), khong chi theo
  level - vi khi nhieu/tat ca kenh deu Critical, sap theo level khong con
  phan biet duoc dau la te nhat.
- 2 tab PHAN TICH DOC LAP HOAN TOAN theo san pham (First Loan / Re-loan) -
  moi tab co Tong quan/Diem nghen/Insight/De xuat rieng (LLM goi rieng cho
  tung san pham) - khong con tab "Tat ca" gop chung, vi 2 san pham co dac
  tinh funnel khac han nhau (First Loan = thu hut khach moi, Re-loan = giu
  chan khach cu), de chung gay so sanh sai lech.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from app.metrics import FUNNEL_STEPS, KPI_DIRECTIONS, KPI_LABELS, ROLLING_WINDOW_DAYS

TEMPLATE_DIR = Path(__file__).parent / "templates"

LEVEL_ORDER = {"critical": 0, "warning": 1, "normal": 2, "chua_du_du_lieu": 3, "khong_ap_dung": 3}

# Thu tu KPI theo HANH TRINH KHACH HANG (Tiep can -> Dang ky -> Duyet don ->
# Giai ngan -> Sau giai ngan), khop 5 buoc funnel trong app/metrics.py. Dung de
# sap thu tu cac bieu do o Muc 3 (Diem nghen) va gan nhan buoc cho tung bieu do.
KPI_JOURNEY = [
    ("reach_count", "1. Tiếp cận"), ("cpl", "1. Tiếp cận"),
    ("lead_to_registration_rate", "2. Đăng ký"), ("registered_count", "2. Đăng ký"),
    ("approval_rate", "3. Duyệt đơn"), ("approved_count", "3. Duyệt đơn"),
    ("disbursed_count", "4. Giải ngân"), ("disbursed_amount", "4. Giải ngân"), ("cpa", "4. Giải ngân"),
    ("early_settlement_rate", "5. Sau giải ngân"), ("roi_outstanding", "5. Sau giải ngân"),
]
KPI_JOURNEY_INDEX = {kpi: i for i, (kpi, _) in enumerate(KPI_JOURNEY)}
KPI_STAGE = dict(KPI_JOURNEY)

# KPI LUON co bieu do o Muc 3 du chua bi canh bao: moi buoc cua hanh trinh 1 bieu do dai dien, de
# ngay khoe manh (it/khong canh bao) nguoi xem van thay du Buoc 1 -> 5 thay vi bi "thieu dau".
# KPI dang canh bao hom nay duoc THEM vao (toi da max_kpis) va danh dau noi bat tren giao dien.
ALWAYS_CHART_KPIS = [
    "reach_count",                # Buoc 1. Tiep can
    "lead_to_registration_rate",  # Buoc 2. Dang ky
    "approval_rate",              # Buoc 3. Duyet don
    "disbursed_amount",           # Buoc 4. Giai ngan
    "roi_outstanding",            # Buoc 5. Sau giai ngan
]
# Don vi hien thi truc/tooltip: "vnd" (dong, nguyen), "vnd_million" (trieu dong)
KPI_UNIT = {"cpl": "vnd", "cpa": "vnd", "disbursed_amount": "vnd_million"}


def _campaign_status(anomalies_df) -> list[dict]:
    """Voi moi kenh, lay muc canh bao TE NHAT trong ngay, sap xep theo DO
    NGHIEM TRONG (bad_z lon nhat truoc) chu khong chi theo level - de khi
    nhieu kenh cung Critical van biet kenh nao dang te nhat."""
    if anomalies_df.empty:
        return []
    out = []
    for campaign, g in anomalies_df.groupby("campaign_name"):
        flagged = g[g["level"].isin(("critical", "warning"))]
        if flagged.empty:
            out.append(dict(campaign_name=campaign, status="normal", worst_kpi_label=None, worst_bad_z=0.0))
            continue
        worst = flagged.loc[flagged["bad_z"].idxmax()]
        out.append(dict(
            campaign_name=campaign, status=worst["level"],
            worst_kpi_label=worst["kpi_label"], worst_bad_z=float(worst["bad_z"]),
        ))
    out.sort(key=lambda r: (LEVEL_ORDER.get(r["status"], 9), -r["worst_bad_z"]))
    return out


def _funnel_totals(funnel_summary_df) -> list[dict]:
    totals = []
    for col, label in FUNNEL_STEPS:
        if col in funnel_summary_df.columns:
            totals.append({"step": label, "count": int(funnel_summary_df[col].sum())})
    return totals


def _funnel_by_campaign(funnel_summary_df) -> list[dict]:
    """Khong theo doi ty le rot Duyet->Giai ngan: don da duyet gan nhu chac
    chan se giai ngan, phan sai lech chi la do do tre xu ly cohort (giai
    ngan theo ngay DUYET, khong phai ngay TAO DON), khong phai tin hieu
    kinh doanh that su - theo yeu cau nguoi dung, khong dua chi so nay len
    dashboard nua."""
    rows = []
    for _, r in funnel_summary_df.iterrows():
        rows.append(dict(
            campaign_name=r["campaign_name"],
            channel=r.get("channel", ""),
            # So lieu tho tung buoc funnel theo kenh - de client (JS) tu
            # cong lai khi ap dung bo loc kenh.
            steps={col: int(r[col]) if col in r and pd.notna(r[col]) else 0 for col, _ in FUNNEL_STEPS},
            dropoff_reach_to_register=r.get("dropoff_reach_to_register"),
            dropoff_register_to_approve=r.get("dropoff_register_to_approve"),
        ))
    return rows


def _cost_chart_data(funnel_summary_df) -> list[dict]:
    rows = []
    for _, r in funnel_summary_df.iterrows():
        ad_spend = float(r["ad_spend"]) if pd.notna(r.get("ad_spend")) else 0.0
        disbursed_amount = float(r["disbursed_amount"]) if pd.notna(r.get("disbursed_amount")) else 0.0
        # Ty le DOANH SO / CHI PHI (bao nhieu dong giai ngan tren 1 dong chi
        # phi bo ra) - de hieu hon ty le nguoc lai (chi phi/doanh so nho le).
        revenue_to_cost_ratio = round(disbursed_amount / ad_spend, 1) if ad_spend else None
        rows.append(dict(
            campaign_name=r["campaign_name"],
            cpl=round(float(r["cpl"]), 0) if pd.notna(r.get("cpl")) else None,
            cpa=round(float(r["cpa"]), 0) if pd.notna(r.get("cpa")) else None,
            ad_spend=round(ad_spend, 0),
            disbursed_amount=round(disbursed_amount, 0),
            revenue_to_cost_ratio=revenue_to_cost_ratio,
        ))
    return rows


def _kpi_trend_charts(kpi_df, anomalies_df, as_of_date, max_kpis: int = 8, days: int = 30) -> list[dict]:
    """Moi buoc hanh trinh luon co 1 bieu do dai dien (ALWAYS_CHART_KPIS), cong them moi KPI dang
    co canh bao hom nay (toi da max_kpis, uu tien bad_z te nhat). Moi duong trong bieu do la 1
    kenh (30 ngay gan nhat); series cua kenh dang canh bao mang level critical/warning de giao dien
    danh dau. Filter theo kenh (Tat ca / tung kenh) duoc ap dung o phia client (JS), du lieu goc
    tra ve du ca cho moi kenh."""
    if kpi_df.empty:
        return []
    if anomalies_df.empty:
        flagged = pd.DataFrame(columns=["kpi", "level", "bad_z", "campaign_name"])
    else:
        flagged = anomalies_df[anomalies_df["level"].isin(["critical", "warning"])].copy()

    kpi_worst = flagged.groupby("kpi")["bad_z"].max().sort_values(ascending=False)
    # Chon max_kpis KPI nghiem trong nhat + KPI luon hien, roi sap lai theo thu tu hanh trinh
    chosen = set(kpi_worst.head(max_kpis).index.tolist()) | set(ALWAYS_CHART_KPIS)
    top_kpis = sorted(chosen, key=lambda k: KPI_JOURNEY_INDEX.get(k, 99))

    window = kpi_df[kpi_df["date"] <= as_of_date].copy()
    all_dates = sorted(window["date"].unique())[-days:]
    window = window[window["date"].isin(all_dates)]

    charts = []
    for kpi in top_kpis:
        # Bo qua KPI khong co du lieu nao trong khoang hien thi (vd chi so khong ap dung cho san pham nay)
        if kpi not in window.columns or not pd.to_numeric(window[kpi], errors="coerce").notna().any():
            continue
        kpi_label = KPI_LABELS[kpi]
        kpi_type = KPI_DIRECTIONS[kpi]
        flagged_today_by_channel = {
            r["campaign_name"]: r["level"]
            for _, r in flagged[flagged["kpi"] == kpi].iterrows()
        }
        series = []
        for channel, g in window.groupby("campaign_name"):
            g = g.set_index("date").reindex(all_dates)
            series.append(dict(
                channel=channel,
                values=[None if pd.isna(v) else round(float(v), 4) for v in g[kpi]],
                level=flagged_today_by_channel.get(channel, "normal"),
            ))
        charts.append(dict(
            kpi=kpi, kpi_label=kpi_label, kpi_type=kpi_type, stage=KPI_STAGE.get(kpi, ""),
            unit=KPI_UNIT.get(kpi, ""), dates=[str(d) for d in all_dates], series=series,
        ))
    return charts


def _anomalies_table(anomalies_df) -> list[dict]:
    if anomalies_df.empty:
        return []
    flagged = anomalies_df[anomalies_df["level"].isin(["warning", "critical"])].copy()
    flagged = flagged.sort_values(["level", "bad_z"], key=lambda s: s.map(LEVEL_ORDER) if s.name == "level" else -s)
    rows = []
    for _, r in flagged.iterrows():
        rows.append(dict(campaign_name=r["campaign_name"], kpi_label=r["kpi_label"], level=r["level"]))
    return rows


def _journey_by_campaign(funnel_summary_df, product: str) -> list[dict]:
    """So lieu hanh trinh truoc dang ky (Install->SDT->Lead->Dang ky, First
    Loan) hoac chu ky tai vay (Re-loan) - TRA VE THEO TUNG KENH (khong gop
    san) de client (JS) tu tinh lai khi ap dung bo loc kenh (Muc 3, Diem
    nghen)."""
    cols = (
        ["install_count", "phone_verified_count",
         "avg_lead_to_install_hours", "avg_install_to_phone_hours", "avg_phone_to_apply_hours"]
        if product == "First Loan" else ["avg_reloan_cadence_days"]
    )
    rows = []
    for _, r in funnel_summary_df.iterrows():
        row = {"campaign_name": r["campaign_name"]}
        for c in cols:
            v = r.get(c)
            row[c] = None if v is None or pd.isna(v) else float(v)
        rows.append(row)
    return rows


HEADLINE_COLS = [
    "reach_count", "registered_count", "approved_count", "disbursed_count", "disbursed_amount", "ad_spend",
    "disbursed_acq_cost",
]


def _headline_by_channel(product_kpi_df, as_of_date) -> list[dict]:
    """So lieu goc TUNG KENH cho khoi the chi so dieu hanh dau trang: hom nay,
    hom qua (ngay lien truoc) va trung binh 7 ngay truoc do. Client (JS) tu
    cong lai theo bo loc kenh cua khoi the nay roi tinh ty le (ty le duyet,
    gia tri khoan vay TB, CPL) tu tong."""
    prior_dates = sorted(d for d in product_kpi_df["date"].unique() if d < as_of_date)
    prev_date = prior_dates[-1] if prior_dates else None
    base_dates = prior_dates[-ROLLING_WINDOW_DAYS:]

    def _totals(df, agg):
        if df.empty:
            return None
        return {c: float(getattr(df[c], agg)()) for c in HEADLINE_COLS}

    rows = []
    for channel, g in product_kpi_df.groupby("campaign_name"):
        rows.append(dict(
            campaign_name=channel,
            today=_totals(g[g["date"] == as_of_date], "sum"),
            prev=_totals(g[g["date"] == prev_date], "sum") if prev_date is not None else None,
            base7=_totals(g[g["date"].isin(base_dates)], "mean"),
        ))
    return rows


def _quick_summary(anomalies_df, llm_result: dict) -> dict:
    """Hop tom tat len dau trang: top 3 van de nghiem trong nhat (kem ly do)
    + top 3 de xuat uu tien cao - CHI trong pham vi 1 san pham, cho nguoi
    chi co 30s doc."""
    highlights_by_campaign = {
        h.get("campaign_name"): h.get("insight_1_cau")
        for h in (llm_result.get("critical_highlights") or [])
    }

    top_issues = []
    if not anomalies_df.empty:
        flagged = anomalies_df[anomalies_df["level"].isin(["critical", "warning"])].copy()
        flagged = flagged.sort_values("bad_z", ascending=False).head(3)
        for _, r in flagged.iterrows():
            # Uu tien ly do LLM da viet san (critical_highlights) cho dung
            # kenh nay; neu khong co, dung mo ta so lieu THUC TE (Python
            # tinh) thay vi bo trong hoac bia nguyen nhan.
            # Moi insight chi dung 1 lan (critical_highlights gan theo kenh, khong
            # theo KPI) - tranh lap nguyen 1 cau cho 2 KPI khac nhau cua cung kenh.
            reason = highlights_by_campaign.pop(r["campaign_name"], None)
            if not reason:
                pct = _pct_diff(r["value"], r["baseline_mean"])
                if pct is None:
                    reason = f"{fmt_value_for_reason(r['value'])} — chênh lệch đáng kể so với mức thường ngày"
                else:
                    word = "cao hơn" if pct > 0 else "thấp hơn"
                    reason = f"{fmt_value_for_reason(r['value'])} — {word} {abs(pct):.0f}% so với mức thường ngày"
                    if r["kpi_type"] == "cost" and pct > 0:
                        reason += " (cần đối chiếu doanh số giải ngân trước khi kết luận tốt/xấu)"
            top_issues.append(dict(
                campaign_name=r["campaign_name"], kpi_label=r["kpi_label"], level=r["level"], reason=reason,
            ))

    suggestions = llm_result.get("suggestions") or []
    top_suggestions = [s for s in suggestions if s.get("uu_tien") == "cao"][:3]
    return dict(top_issues=top_issues, top_suggestions=top_suggestions)


def _pct_diff(value, baseline):
    """% chenh lech so voi muc binh thuong - ngon ngu kinh doanh, thay cho
    do lech chuan/z-score o moi noi hien thi cho nguoi dung."""
    if pd.isna(value) or pd.isna(baseline) or baseline == 0:
        return None
    return (value - baseline) / baseline * 100


def fmt_value_for_reason(v) -> str:
    if pd.isna(v):
        return "-"
    return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"


def _build_view(product: str, product_kpi_df, funnel_summary_df, anomalies_df, llm_result: dict, as_of_date,
                contributions: dict) -> dict:
    return dict(
        product=product,
        contributions=list(contributions.values()),
        quick_summary=_quick_summary(anomalies_df, llm_result),
        campaigns_status=_campaign_status(anomalies_df),
        funnel_step_defs=[{"col": col, "label": label} for col, label in FUNNEL_STEPS],
        funnel_totals=_funnel_totals(funnel_summary_df),
        funnel_by_campaign=_funnel_by_campaign(funnel_summary_df),
        headline_by_channel=_headline_by_channel(product_kpi_df, as_of_date),
        kpi_trend_charts=_kpi_trend_charts(product_kpi_df, anomalies_df, as_of_date),
        cost_chart=_cost_chart_data(funnel_summary_df),
        anomalies_table=_anomalies_table(anomalies_df),
        journey_by_campaign=_journey_by_campaign(funnel_summary_df, product),
        overview_text=llm_result.get("tong_quan"),
        bottlenecks=llm_result.get("diem_nghen") or [],
        cost_text=llm_result.get("hieu_qua_chi_phi"),
        insight_marketing=llm_result.get("insight_marketing") or [],
        insight_sale=llm_result.get("insight_sale") or [],
        suggestions=llm_result.get("suggestions") or [],
        critical_highlights=llm_result.get("critical_highlights") or [],
    )


def prepare_dashboard_context(as_of_date, per_product: dict) -> dict:
    products = list(per_product.keys())
    views = {
        p: _build_view(p, v["kpi_df"], v["summary"], v["anomalies"], v["llm_result"], as_of_date, v["contributions"])
        for p, v in per_product.items()
    }
    return dict(
        as_of_date=str(as_of_date),
        tab_labels=products,
        views=views,
    )


def render_dashboard_html(context: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("dashboard.html")
    # Du lieu (ten kenh tu Google Sheet, chu do LLM tra ve) nam trong the <script>. Chi thay "</"
    # la KHONG du: chuoi "<!--<script>" van lam trinh duyet nuot mat the dong script va Dashboard
    # bi trang. Escape han <, >, & thanh \uXXXX (van la JSON hop le, JSON.parse tra lai dung ky tu
    # goc) cung 2 ky tu ngat dong U+2028/U+2029.
    raw_json = (
        json.dumps(context, ensure_ascii=False, default=str)
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )
    return template.render(**context, context_json=raw_json)
