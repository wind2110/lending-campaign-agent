"""Them sheet fact_lead_cost (chi phi lead thuc te theo ngay x chien dich) vao data/full_schema_mock.xlsx.

Truoc day khong co bang chi phi quang cao nen CPL phai tinh xap xi tu chi phi cua cac ho so dang ky
(lech nhom khach voi so lead cung ngay). Bang nay thay the: CPL = lead_cost / so lead cung ngay/kenh.

Cho khop voi bang loan_application_pnl: chi phi lead tung ho so trong pnl (15k-60k, TB ~37k o moi kenh)
la GIA CUA LEAD do. Nen gia moi lead trong bang nay duoc boc ngau nhien tu chinh phan phoi pnl.lead_cost
cua kenh (khong doi theo viec lead co chuyen doi hay khong), roi cong theo ngay. Vi vay CPL trung binh
kenh ~ pnl.lead_cost trung binh, va tong chi phi lead moi ngay >= tong pnl.lead_cost cua cac lead da chuyen doi.
Ngay cuoi giu kich ban tang CPL da co san (Facebook, TikTok, Partner App, Zalo), Google Ads binh thuong;
dat SPIKE_LAST_DAY = 1.0 cho moi kenh neu khong muon kich ban nay.
Chay: ./venv/Scripts/python.exe scripts/add_lead_cost_table.py  (ghi ra data/full_schema_mock_v2.xlsx)
"""
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

SRC = Path("data/full_schema_mock.xlsx")
OUT = Path("data/full_schema_mock_v2.xlsx")
SHEET = "fact_lead_cost"
COLUMNS = ["cost_date", "channel", "campaign_id", "campaign_name", "lead_cost"]
rng = np.random.default_rng(20260922)

SPIKE_LAST_DAY = {"Facebook Ads": 1.835, "Google Ads": 1.0, "Partner App": 1.473, "TikTok Ads": 1.873, "Zalo Ads": 1.628}
DAILY_NOISE, SPIKE_NOISE = 0.06, 0.02  # bien dong gia dau thau hang ngay (chung cho ca kenh)

lead = pd.read_excel(SRC, sheet_name="fact_lead")
loan = pd.read_excel(SRC, sheet_name="fact_loan")
pnl = pd.read_excel(SRC, sheet_name="loan_application_pnl")
lead["cost_date"] = lead["create_at"].dt.date
pool_df = loan.merge(pnl[["loan_application_id", "lead_cost"]], left_on="application_id", right_on="loan_application_id")
pool = {ch: g["lead_cost"].to_numpy() for ch, g in pool_df.groupby("channel")}

daily = (lead.groupby(["cost_date", "channel", "campaign_id", "campaign_name"]).size()
         .reset_index(name="n_leads").sort_values(["cost_date", "channel"]).reset_index(drop=True))
last_day = daily["cost_date"].max()

costs = []
for r in daily.itertuples():
    is_last = r.cost_date == last_day
    factor = (SPIKE_LAST_DAY[r.channel] if is_last else 1.0) * np.exp(rng.normal(0, SPIKE_NOISE if is_last else DAILY_NOISE))
    costs.append(rng.choice(pool[r.channel], size=r.n_leads).sum() * factor)
daily["lead_cost"] = (np.round(np.array(costs) / 100) * 100).astype("int64")
table = daily[COLUMNS]

wb = load_workbook(SRC)
if SHEET in wb.sheetnames:
    del wb[SHEET]
ws = wb.create_sheet(SHEET)
ws.append(COLUMNS)
for r in table.itertuples(index=False):
    ws.append(list(r))
wb.save(OUT)

# Kiem tra: doc lai file vua ghi
S_new = pd.read_excel(OUT, sheet_name=None)
S_old = pd.read_excel(SRC, sheet_name=None)
for k in S_old:
    if k != SHEET:
        pd.testing.assert_frame_equal(S_old[k], S_new[k], check_dtype=False), f"sheet {k} bi thay doi"
c = S_new[SHEET].copy()
c["cost_date"] = pd.to_datetime(c["cost_date"]).dt.date
assert list(c.columns) == COLUMNS and len(c) == len(daily) and (c["lead_cost"] > 0).all()

# Rang buoc doi chieu voi pnl: tong chi phi lead moi kenh >= tong pnl.lead_cost cua cac ho so cua kenh do
# (fact_loan khong con lead_id nen khong doi chieu duoc theo tung ngay)
assert all(c.loc[c["channel"] == ch, "lead_cost"].sum() >= pool[ch].sum() for ch in pool), "tong chi phi lead < chi phi lead trong pnl"
chk = c.merge(daily[["cost_date", "channel", "n_leads"]], on=["cost_date", "channel"])
chk["cpl"] = chk["lead_cost"] / chk["n_leads"]

disb = loan[loan["disbursement_date"].notna()]
print(f"Da them sheet {SHEET}: {len(c)} dong ({c['cost_date'].nunique()} ngay x {c['channel'].nunique()} kenh); cac sheet khac khong doi.")
print("  kenh          | CPL TB (bang moi) | pnl.lead_cost TB | tong moi / tong pnl | CPL 7 ngay truoc 31/8 -> 31/8 | chi phi lead / khoan giai ngan | % doanh so")
for ch, g in chk.groupby("channel"):
    g = g.sort_values("cost_date")
    prev7 = g.iloc[-8:-1]["cpl"].mean()
    n_disb = int((disb["channel"] == ch).sum())
    disb_amt = disb.loc[disb["channel"] == ch, "loan_amount"].sum()
    tot = g["lead_cost"].sum()
    print(f"  {ch:13s} | {tot / g['n_leads'].sum():8,.0f} | {pool[ch].mean():8,.0f} | {tot / pool[ch].sum():5.2f}x | {prev7:8,.0f} -> {g.iloc[-1]['cpl']:8,.0f} ({g.iloc[-1]['cpl'] / prev7 - 1:+.0%}) | {tot / n_disb:9,.0f} | {tot / disb_amt:.2%}")
print(f"Da ghi: {OUT}")
