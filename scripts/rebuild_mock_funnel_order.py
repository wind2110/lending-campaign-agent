"""Tai tao data/full_schema_mock.xlsx -> data/full_schema_mock_v2.xlsx theo funnel moi.

Thu tu First Loan: Lead (tuong tac quang cao) -> Cai app -> Nhap SDT -> Xac thuc SDT -> Dang ky vay.
Re-loan (Zalo Ads) khong qua buoc cai app. Bo kenh Broker Network.
Sua cac ho so fact_loan.create_at trung dung 20:00:00 thanh gio khac nhau, hop ly.
Script lich su: doc tu ban sao luu truoc khi bo cot fact_loan.lead_id (xem drop_loan_lead_id.py).
Chay: ./venv/Scripts/python.exe scripts/rebuild_mock_funnel_order.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("data/full_schema_mock.before_rebuild.xlsx")
OUT = Path("data/full_schema_mock_v2.xlsx")
BROKER = "Broker Network"
LAST_DAY = pd.Timestamp("2026-08-31")
CUTOFF = LAST_DAY + pd.Timedelta(hours=23, minutes=59, seconds=59)
FIRST_DAY = pd.Timestamp("2026-08-01")
rng = np.random.default_rng(20260831)

# Ty le lead (First Loan, chua dang ky vay) co cai app, theo kenh; roi den buoc nhap/xac thuc SDT
INSTALL_RATE = {"Facebook Ads": 0.38, "Google Ads": 0.44, "TikTok Ads": 0.32, "Partner App": 0.40}
P_CAPTURE, P_VERIFY = 0.68, 0.78
MIN_GAP_FL, MIN_GAP_RL = 20.0, 5.0  # phut toi thieu tu lead den dang ky


def td(minutes):
    return np.round(np.asarray(minutes, float) * 6e10).astype("int64").astype("timedelta64[ns]")


def floor_s(a):
    return a.astype("datetime64[s]").astype("datetime64[ns]")


S = pd.read_excel(SRC, sheet_name=None)
sheet_order = list(S)
cols = {k: list(v.columns) for k, v in S.items()}
lead, loan = S["fact_lead"].copy(), S["fact_loan"].copy()
reject, pnl = S["fact_reject"].copy(), S["loan_application_pnl"].copy()
foot, cust = S["fact_digital_footprint"].copy(), S["dim_customer"].copy()
n_orig_67 = int(((loan.create_at.dt.hour == 20) & (loan.create_at.dt.minute == 0) & (loan.create_at.dt.second == 0)).sum())

# 0. form_filling_time am (loi du lieu mock) -> thoi gian duong hop ly
neg = foot["form_filling_time"] < 0
foot.loc[neg, "form_filling_time"] = rng.integers(90, 240, int(neg.sum()))

# 1. Bo kenh Broker Network o moi bang
b_loans = loan[loan["channel"] == BROKER]
b_ids, b_cust = set(b_loans["application_id"]), set(b_loans["Customer_id"].dropna())
lead = lead[lead["channel"] != BROKER].reset_index(drop=True)
loan = loan[loan["channel"] != BROKER].reset_index(drop=True)
reject = reject[~reject["loan_application_id"].isin(b_ids)].reset_index(drop=True)
pnl = pnl[~pnl["loan_application_id"].isin(b_ids)].reset_index(drop=True)
foot = foot[~foot["loan_application_id"].isin(b_ids)].reset_index(drop=True)
b_cust -= set(loan["Customer_id"].dropna()) | set(lead["Customer_id"].dropna())
cust = cust[~cust["customer_id"].isin(b_cust)].reset_index(drop=True)

loan["lead_at"] = loan["lead_id"].map(lead.set_index("lead_id")["create_at"])
is_fl = loan["product_name"].eq("First Loan").to_numpy()
need = np.where(is_fl, MIN_GAP_FL, MIN_GAP_RL)

# 2. Sua cac ho so cung timestamp 20:00:00 (tat ca la ngay 31/08): gio ngau nhien nhung hop ly
t = loan["create_at"]
m67 = ((t.dt.hour == 20) & (t.dt.minute == 0) & (t.dt.second == 0)).to_numpy()
n_fixed = int(m67.sum())
eod = LAST_DAY + pd.Timedelta(hours=23, minutes=59)
for i in np.flatnonzero(m67):
    old_create = loan.at[i, "create_at"]
    lo = max(loan.at[i, "lead_at"] + pd.Timedelta(minutes=need[i]), LAST_DAY + pd.Timedelta(minutes=1))
    hi = LAST_DAY + pd.Timedelta(hours=23, minutes=50)
    new_create = (lo + (hi - lo) * rng.random()).floor("s")
    old_disb = loan.at[i, "disbursement_date"]
    if pd.notna(old_disb):
        delay, due_delta = old_disb - old_create, loan.at[i, "last_duedate"] - old_disb
        new_disb = new_create + delay
        if new_disb > eod - pd.Timedelta(minutes=1):  # giai ngan phai trong cung ngay
            new_disb = new_create + (eod - new_create) * rng.uniform(0.3, 0.9)
        new_disb = new_disb.floor("s")
        loan.at[i, "disbursement_date"] = new_disb
        loan.at[i, "last_duedate"] = (new_disb + due_delta).floor("s")
    loan.at[i, "create_at"] = new_create

# 3. Lead phai xuat hien TRUOC (dang ky - thoi gian toi thieu): keo lead som hon neu chua du
gap = ((loan["create_at"] - loan["lead_at"]).dt.total_seconds() / 60).to_numpy()
bad = np.flatnonzero(gap < need)
new_lead = (loan.loc[bad, "create_at"] - pd.to_timedelta(rng.uniform(need[bad], need[bad] + 25), unit="m")).dt.floor("min")
lead_map = dict(zip(loan.loc[bad, "lead_id"], new_lead))
m = lead["lead_id"].map(lead_map)
lead.loc[m.notna(), "create_at"] = m[m.notna()]
n_lead_shifted = int(m.notna().sum())
loan["lead_at"] = loan["lead_id"].map(lead.set_index("lead_id")["create_at"])

# 4. Chuoi Install -> SDT -> Xac thuc cho khach First Loan da dang ky (tinh nguoc tu thoi diem dang ky)
fl = loan[is_fl]
form_min = np.maximum(fl["application_id"].map(foot.set_index("loan_application_id")["form_filling_time"]).to_numpy(float) / 60, 1.0)
G = ((fl["create_at"] - fl["lead_at"]).dt.total_seconds() / 60).to_numpy()
n = len(fl)
d4min = form_min + 1.0
avail = G - d4min - 2.0
d2 = np.clip(rng.lognormal(np.log(2.5), 0.6, n), 0.5, 15)   # cai app -> nhap SDT
d3 = np.clip(rng.lognormal(np.log(1.5), 0.7, n), 0.3, 12)   # nhap SDT -> xac thuc OTP
scale = np.minimum(1.0, 0.5 * avail / (d2 + d3))
d2, d3 = d2 * scale, d3 * scale
slack = avail - d2 - d3
d4 = d4min + np.minimum(rng.lognormal(np.log(12), 1.0, n), 0.5 * slack)  # xac thuc -> dang ky
create = fl["create_at"].to_numpy("datetime64[ns]")
conv = pd.DataFrame({
    "lead_id": fl["lead_id"].to_numpy(), "channel": fl["channel"].to_numpy(),
    "install_time": floor_s(create - td(d4 + d3 + d2)),
    "phone_captured_time": floor_s(create - td(d4 + d3)),
    "phone_verified_time": floor_s(create - td(d4)),
})

# 5. Lead First Loan chua dang ky: mot phan cai app, roi rot dan theo tung buoc; cat tai moc cuoi du lieu
nc = lead[(lead["product_id"] == "PRD-FL") & ~lead["lead_id"].isin(fl["lead_id"])]
nc = nc[rng.random(len(nc)) < nc["channel"].map(INSTALL_RATE).to_numpy()]
k = len(nc)
ins = floor_s(nc["create_at"].to_numpy("datetime64[ns]") + td(np.clip(rng.lognormal(np.log(300), 1.2, k), 2, 72 * 60)))
cap_ok = rng.random(k) < P_CAPTURE
ver_ok = cap_ok & (rng.random(k) < P_VERIFY)
cap = floor_s(ins + td(np.clip(rng.lognormal(np.log(2.5), 0.6, k), 0.5, 15)))
ver = floor_s(cap + td(np.clip(rng.lognormal(np.log(1.5), 0.7, k), 0.3, 12)))
nat = np.datetime64("NaT", "ns")
cap, ver = np.where(cap_ok, cap, nat), np.where(ver_ok, ver, nat)
cut = np.datetime64(CUTOFF.to_datetime64(), "ns")
cap = np.where(cap > cut, nat, cap)
ver = np.where((ver > cut) | np.isnat(cap), nat, ver)
nonconv = pd.DataFrame({
    "lead_id": nc["lead_id"].to_numpy(), "channel": nc["channel"].to_numpy(),
    "install_time": ins, "phone_captured_time": cap, "phone_verified_time": ver,
})
nonconv = nonconv[nonconv["install_time"] <= cut]

install = pd.concat([conv, nonconv], ignore_index=True).sort_values("lead_id").reset_index(drop=True)
install.insert(0, "install_id", ["INST-%07d" % (i + 1) for i in range(len(install))])
install = install[cols["fact_app_install"]]

# 6. dim_customer: khach First Loan mo tai khoan luc xac thuc SDT; moi khach da vay deu da co app
ver_by_lead = install.set_index("lead_id")["phone_verified_time"]
cust = cust.set_index("customer_id")
cust.loc[fl["Customer_id"].to_numpy(), "Customer_open_date"] = fl["lead_id"].map(ver_by_lead).dt.normalize().to_numpy()
cust.loc[cust.index.isin(loan["Customer_id"]), "has_app"] = True
cust = cust.reset_index()[cols["dim_customer"]]

# 7. Kiem tra rang buoc
lead_at = lead.set_index("lead_id")["create_at"]
inst_i = install.set_index("lead_id")
flc = fl.set_index("lead_id")
j = inst_i.loc[flc.index]
assert ((j["install_time"] > lead_at.loc[flc.index]) & (j["phone_captured_time"] > j["install_time"])
        & (j["phone_verified_time"] > j["phone_captured_time"]) & (flc["create_at"] > j["phone_verified_time"])).all(), "chuoi FL sai thu tu"
assert (loan["create_at"] > loan["lead_id"].map(lead_at)).all(), "lead phai truoc dang ky"
ok = install["phone_captured_time"].notna() | install["phone_verified_time"].isna()
assert ok.all() and (install["phone_captured_time"].isna() | (install["phone_captured_time"] > install["install_time"])).all()
assert (install["install_time"] > install["lead_id"].map(lead_at)).all() and install["lead_id"].is_unique
assert install["install_time"].max() <= CUTOFF and install["lead_id"].isin(lead["lead_id"]).all()
assert not install["channel"].isin([BROKER, "Zalo Ads"]).any(), "Re-loan/Broker khong duoc co cai app"
assert not ((loan["create_at"].dt.hour == 20) & (loan["create_at"].dt.minute == 0) & (loan["create_at"].dt.second == 0)).any()
d = loan.dropna(subset=["disbursement_date"])
dh = (d["disbursement_date"] - d["create_at"]).dt.total_seconds() / 3600
assert (dh > 0).all() and (dh <= 2.0001).all() and (d["disbursement_date"].dt.date == d["create_at"].dt.date).all()
for df, c in [(lead, "channel"), (loan, "channel")]:
    assert (df[c] != BROKER).all()
assert set(pnl["loan_application_id"]) == set(loan["application_id"]) == set(foot["loan_application_id"])
assert set(reject["loan_application_id"]) <= set(loan["application_id"])
assert not any(BROKER.lower() in str(x).lower() or "broker" in str(x).lower()
               for df in (lead, loan) for c in df.select_dtypes("object") for x in df[c].dropna().unique())

# 8. Ghi file
final = dict(S)
final.update({
    "fact_lead": lead[cols["fact_lead"]], "fact_loan": loan[cols["fact_loan"]], "fact_reject": reject,
    "loan_application_pnl": pnl, "fact_digital_footprint": foot, "dim_customer": cust, "fact_app_install": install,
})
with pd.ExcelWriter(OUT, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS") as w:
    for name in sheet_order:
        final[name].to_excel(w, sheet_name=name, index=False)

new67 = loan[m67]
print(f"Ho so cung 20:00 ban dau: {n_orig_67} | bo cung Broker: {n_orig_67 - n_fixed} | da sua gio: {n_fixed}")
print(f"  gio moi: {new67['create_at'].dt.strftime('%H:%M:%S').nunique()} gia tri khac nhau / {n_fixed}; "
      f"tu {new67['create_at'].min().strftime('%H:%M:%S')} den {new67['create_at'].max().strftime('%H:%M:%S')}")
print(f"Lead bi keo som hon de dam bao truoc dang ky: {n_lead_shifted} | form_filling_time am da sua: {int(neg.sum())}")
fl_leads = int((lead['product_id'] == 'PRD-FL').sum())
print(f"Funnel First Loan: lead {fl_leads} -> cai app {len(install)} -> nhap SDT {install['phone_captured_time'].notna().sum()} "
      f"-> xac thuc {install['phone_verified_time'].notna().sum()} -> dang ky vay {len(fl)}")
print(f"Loan {len(loan)} (FL {len(fl)}, RL {int((~is_fl).sum())}) | lead {len(lead)} | customer {len(cust)} | kenh: {sorted(loan['channel'].unique())}")
print(f"Da ghi: {OUT}")
