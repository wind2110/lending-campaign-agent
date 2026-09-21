"""Dieu phoi toan bo luong hang ngay: doc du lieu -> tinh KPI/baseline/bat
thuong -> LLM dien giai RIENG cho tung san pham -> build dashboard -> gui
email. Duoc goi boi ca route thu cong (POST /api/refresh) lan job tu dong
hang ngay (scheduler.py).

2 san pham (First Loan / Re-loan) duoc phan tich HOAN TOAN DOC LAP - moi
san pham 1 lan goi LLM rieng - vi dac tinh kinh doanh khac han nhau, gop
chung se gay so sanh sai lech (xem thao luan voi user).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.data_source import load_funnel_history
from app.metrics import (
    build_funnel_summary, compute_channel_contributions, compute_daily_kpis, detect_anomalies, get_latest_date,
)
from app.llm_insight import interpret_daily
from app.dashboard import prepare_dashboard_context, render_dashboard_html
from app.notify import send_daily_email, is_email_configured

DASHBOARD_CACHE_PATH = Path("data/latest_dashboard.html")
INSIGHT_CACHE_PATH = Path("data/latest_insight.json")


def run_daily_pipeline() -> dict:
    df = load_funnel_history()
    kpi_df = compute_daily_kpis(df)
    as_of_date = get_latest_date(kpi_df)

    products = sorted(p for p in kpi_df["product"].dropna().unique().tolist() if p and p != "Khac")
    if not products:
        products = ["Tat ca"]

    per_product = {}
    all_anomalies_frames = []
    for product in products:
        product_kpi_df = kpi_df[kpi_df["product"] == product]
        anomalies = detect_anomalies(product_kpi_df, as_of_date=as_of_date)
        summary = build_funnel_summary(product_kpi_df, as_of_date=as_of_date)
        contributions = compute_channel_contributions(product_kpi_df, as_of_date)
        llm_result = interpret_daily(as_of_date, product, summary, anomalies, contributions)
        per_product[product] = dict(
            kpi_df=product_kpi_df, summary=summary, anomalies=anomalies, llm_result=llm_result,
            contributions=contributions,
        )
        all_anomalies_frames.append(anomalies)

    context = prepare_dashboard_context(as_of_date, per_product)
    html = render_dashboard_html(context)

    DASHBOARD_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_CACHE_PATH.write_text(html, encoding="utf-8")
    INSIGHT_CACHE_PATH.write_text(
        json.dumps(
            {"as_of_date": str(as_of_date), "per_product": {p: v["llm_result"] for p, v in per_product.items()}},
            ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )

    import pandas as pd
    all_anomalies = pd.concat(all_anomalies_frames, ignore_index=True) if all_anomalies_frames else None
    n_critical = int((all_anomalies["level"] == "critical").sum()) if all_anomalies is not None else 0
    n_warning = int((all_anomalies["level"] == "warning").sum()) if all_anomalies is not None else 0

    email_sent = False
    if is_email_configured():
        dashboard_url = os.environ.get("DASHBOARD_BASE_URL", "http://localhost:8080").rstrip("/") + "/"
        combined_llm = {
            "tong_quan": " ".join(v["llm_result"].get("tong_quan") or "" for v in per_product.values()),
            "critical_highlights": [
                h for v in per_product.values() for h in (v["llm_result"].get("critical_highlights") or [])
            ],
        }
        email_sent = send_daily_email(as_of_date, combined_llm, dashboard_url)

    return dict(
        as_of_date=str(as_of_date),
        n_critical=n_critical,
        n_warning=n_warning,
        email_sent=email_sent,
        llm_configured=bool(os.environ.get("LLM_API_KEY")),
    )
