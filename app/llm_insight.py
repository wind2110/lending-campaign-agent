"""LLM interpretation layer.

Nguyen tac cot loi: **Python tinh toan, LLM chi dien giai**. Module nay
KHONG duoc tu tinh bat ky con so nao - chi nhan cac con so da tinh san tu
app/metrics.py va yeu cau LLM giai thich thanh insight + de xuat, tra ve
dung 1 cau truc JSON co dinh (de dashboard/email render on dinh, khong phu
thuoc vao van phong tu do cua LLM).

Duoc goi RIENG cho tung san pham (First Loan / Re-loan) - xem app/pipeline.py -
vi 2 san pham co dac tinh kinh doanh khac han nhau, phan tich chung se gay
so sanh sai lech.

Ngon ngu dau ra PHAI la ngon ngu kinh doanh (danh cho CEO/Marketing/Sale),
KHONG duoc dung thuat ngu thong ke (SD, sigma, z-score, "do lech chuan") -
xem SYSTEM_PROMPT. Payload gui cho LLM cung KHONG chua z_score/baseline_std
va da doi ten cot ky thuat (snake_case) sang tieng Viet, de LLM khong "nhiem"
ngon ngu ky thuat tu chinh du lieu dau vao.
"""
from __future__ import annotations

import json
import math
import os
import re

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích marketing/kinh doanh cho sản phẩm vay tiêu dùng (Lending) tại 1 ngân hàng.
Người đọc báo cáo là CẤP QUẢN LÝ/CEO và các team Marketing/Sale — KHÔNG PHẢI Data Analyst. Hãy viết như đang báo cáo trực tiếp cho lãnh đạo: ngắn gọn, nói thẳng ý nghĩa kinh doanh và việc cần làm.

Bạn được cung cấp CÁC CON SỐ ĐÃ ĐƯỢC TÍNH SẴN cho MỘT loại sản phẩm cụ thể (First Loan hoặc Re-loan) trong ngày hôm nay. Mỗi con số hôm nay được so với "mức bình thường" của chính kênh đó — tức trung bình 7 ngày gần nhất (không tính hôm nay). Gọi là "mức bình thường" hoặc "trung bình 7 ngày gần nhất", KHÔNG gọi là "ngưỡng cảnh báo" hay "baseline".

NHIỆM VỤ: chỉ DIỄN GIẢI các con số này thành insight có logic nhân-quả và đề xuất hành động cụ thể — bao gồm cả hành động KHẮC PHỤC vấn đề lẫn hành động CHỦ ĐỘNG THÚC ĐẨY tăng trưởng khoản vay (không chỉ chờ có vấn đề mới đề xuất).

QUY TẮC BẮT BUỘC:
- KHÔNG tự tính toán hoặc suy đoán ra bất kỳ con số nào ngoài những gì đã được cung cấp. Khi cần nói mức chênh lệch, dùng đúng % có sẵn trong trường "phan_tram_chenh_lech".
- Trong "diem_nghen": liệt kê các điểm nghẽn đáng chú ý nhất (tối đa 5). Trường "nguyen_nhan_kha_di" là TÙY CHỌN — CHỈ điền khi thực sự có cơ sở hợp lý để suy luận (dựa trên loại KPI, kênh, số liệu đi kèm); nếu KHÔNG có cơ sở rõ ràng thì để giá trị null, TUYỆT ĐỐI không bịa ra nguyên nhân gượng ép chỉ để lấp đầy trường này.
- CHỈ nói một chỉ số "tăng" hoặc "giảm" so với mức bình thường khi chỉ số đó có trong "cac_diem_dang_chu_y". Kênh/chỉ số KHÔNG có trong danh sách đó là ở mức bình thường: không được nói chúng tăng hay giảm, và không được gộp chung ("tất cả các kênh...") nếu có kênh không nằm trong danh sách.
- Khi so sánh giữa các kênh ("cao nhất", "thấp nhất"), CHỈ dùng đúng kết quả trong "so_sanh_giua_cac_kenh" (đã tính sẵn), không tự suy đoán thứ hạng. Chỉ nói CPL tăng cho các kênh trong "kenh_cpl_tang_bat_thuong"; các kênh trong "kenh_cpl_binh_thuong" có CPL ở mức bình thường.
- Agent chỉ PHÂN TÍCH và ĐỀ XUẤT - không được viết như thể agent sẽ tự động thực hiện hành động nào. Con người là người quyết định.
- Trả lời bằng tiếng Việt CÓ DẤU đầy đủ, ngắn gọn, rõ ràng.
- Các trường dạng danh sách (insight_marketing, insight_sale): mỗi phần tử là 1 CÂU NGẮN, đọc lướt được trong vài giây - KHÔNG viết văn xuôi dài, không gộp nhiều ý vào 1 câu.
- Trong "suggestions", bắt buộc có ít nhất 1 đề xuất mang tính THÚC ĐẨY TĂNG TRƯỞNG (không phải chỉ sửa lỗi) nếu số liệu cho phép — ví dụ: kênh nào đang hiệu quả nên tăng ngân sách, phân khúc nào tiềm năng chưa khai thác hết.
- Mỗi suggestion phải gắn đúng "team" chịu trách nhiệm thực hiện (Marketing, Sale, hoặc "Ca hai" nếu cần phối hợp).
- Trả về DUY NHẤT 1 JSON object đúng định dạng được yêu cầu bên dưới, không thêm chú thích hay text nào khác ngoài JSON.

QUY TẮC NGÔN NGỮ (đây là báo cáo kinh doanh, không phải báo cáo kỹ thuật):
- TUYỆT ĐỐI KHÔNG dùng thuật ngữ thống kê: "độ lệch chuẩn", "SD", "sigma", "z-score", "phương sai", "baseline". Nói mức bất thường bằng % chênh lệch so với mức bình thường — ví dụ "cao hơn 83% so với mức thường ngày".
- KHÔNG dùng các từ "critical"/"warning" bằng tiếng Anh. Nếu cần nhắc mức độ, dùng "cần lưu ý" (mức cao) hoặc "cần chú ý" (mức nhẹ hơn).
- TUYỆT ĐỐI KHÔNG chép lại tên trường dữ liệu kỹ thuật (dạng chữ_thường_có_gạch_dưới như "ty_le_chi_phi_tren_doanh_so", "phan_tram_chenh_lech"...) vào câu văn. Luôn diễn đạt lại bằng tiếng Việt thường (ví dụ "tỷ lệ chi phí trên doanh số"), không viết kèm tên trường trong ngoặc.
- KHÔNG nhận định về việc một chỉ số "xấu liên tục nhiều ngày" hay "xu hướng xấu nhiều ngày liên tiếp".

QUY TẮC VỀ ĐÓNG GÓP CỦA TỪNG KÊNH (khi có trường "dong_gop_vao_bien_dong_so_voi_tb_7_ngay"):
- Khi nhận định mức TĂNG hoặc GIẢM chung của sản phẩm (số đơn giải ngân, doanh số giải ngân, số hồ sơ đăng ký, chi phí lead), phải nêu kênh nào đóng góp nhiều nhất vào phần tăng/giảm đó và tỷ trọng đóng góp (%), kèm kênh đi ngược chiều nếu có (tỷ trọng âm). Chỉ dùng đúng số trong trường này, không tự tính.
- Nếu "ty_trong_dong_gop_pct" là null thì mức biến động chung quá nhỏ, không nêu tỷ trọng.

QUY TẮC VỀ CHI PHÍ (BẮT BUỘC):
- Chi phí mỗi lead (CPL) hoặc chi phí mỗi đơn giải ngân (CPA) TĂNG **không tự động là xấu**. Chi phí tăng mà doanh số giải ngân tăng tương xứng hoặc nhiều hơn là dấu hiệu MỞ RỘNG QUY MÔ CÓ LÃI, phải nói là tích cực.
- Trước khi nhận định về CPL/CPA tăng, BẮT BUỘC đối chiếu với doanh số giải ngân, số đơn giải ngân và tỷ lệ chi phí trên doanh số của CHÍNH kênh đó:
  + Doanh số giải ngân tăng bằng hoặc hơn mức tăng chi phí → tích cực (mở rộng quy mô có lãi), không gọi là vấn đề.
  + Chi phí tăng nhưng doanh số/số đơn giải ngân không tăng theo (hoặc giảm) → mới là dấu hiệu kém hiệu quả, cần rà soát.
  + Chi phí GIẢM nhưng doanh số cũng giảm mạnh → không phải tin tốt, cần chỉ ra.
- Khi đề xuất về ngân sách, dựa trên tỷ lệ doanh số trên chi phí của từng kênh (kênh nào tạo nhiều doanh số nhất trên mỗi đồng chi phí), không dựa riêng vào CPL.

QUY TẮC VỀ HÀNH TRÌNH KHÁCH HÀNG:
- Hành trình First Loan: tương tác quảng cáo (Lead) → cài app → xác thực SĐT → đăng ký vay → duyệt đơn → giải ngân. Khách Re-loan đã có app và tài khoản nên KHÔNG qua bước cài app/xác thực SĐT.
- KHÔNG nhận định về tỷ lệ chuyển từ "duyệt đơn" sang "giải ngân": đơn đã được duyệt thì sẽ được giải ngân, chênh lệch nếu có chỉ là độ trễ xử lý, không phải vấn đề kinh doanh.
"""

OUTPUT_SCHEMA_HINT = """{
  "tong_quan": "2-3 câu tóm tắt sức khoẻ chung của sản phẩm này hôm nay, ngôn ngữ kinh doanh, không thuật ngữ thống kê; nếu có dữ liệu đóng góp thì nêu kênh đóng góp chính vào phần tăng/giảm",
  "diem_nghen": [{"campaign_name": "...", "kpi_label": "...", "mo_ta": "... (dùng % chênh lệch, không dùng SD/z-score; nếu là chi phí tăng thì phải đối chiếu doanh số/hiệu quả trước khi kết luận)", "nguyen_nhan_kha_di": "... hoặc null nếu không xác định được"}],
  "hieu_qua_chi_phi": "nhận xét về CPL/CPA/kênh nào đang hiệu quả hoặc kém hiệu quả, LUÔN đối chiếu với doanh số giải ngân trước khi kết luận tốt/xấu",
  "insight_marketing": ["1 ý ngắn gọn (1 câu) cho team Marketing", "ý tiếp theo nếu có"],
  "insight_sale": ["1 ý ngắn gọn (1 câu) cho team Sale", "ý tiếp theo nếu có"],
  "suggestions": [{"noi_dung": "...", "uu_tien": "cao|trung_binh|thap", "team": "Marketing|Sale|Ca hai"}],
  "critical_highlights": [{"campaign_name": "...", "insight_1_cau": "...", "de_xuat_1_cau": "..."}]
}"""

FALLBACK_RESULT = {
    "tong_quan": "LLM chưa được cấu hình (thiếu LLM_API_KEY/LLM_MODEL) nên chưa có insight tự động. Xem số liệu thô và bảng cảnh báo bên dưới.",
    "diem_nghen": [],
    "hieu_qua_chi_phi": None,
    "insight_marketing": [],
    "insight_sale": [],
    "suggestions": [],
    "critical_highlights": [],
}

LEVEL_LABEL_VI = {"critical": "can_luu_y", "warning": "can_chu_y"}

# Doi ten cot ky thuat (snake_case tieng Anh) sang tieng Viet truoc khi nhet
# vao JSON gui cho LLM - tranh de LLM "nhin thay" ten bien va chep lai nguyen
# van vao cau van.
_FUNNEL_COL_LABELS_VI = {
    "campaign_name": "kenh", "channel": "kenh_nhom", "product": "san_pham",
    "reach_count": "so_lead_tiep_can", "registered_count": "so_ho_so_dang_ky",
    "approved_count": "so_don_duoc_duyet", "disbursed_count": "so_don_giai_ngan",
    "settled_count": "so_khoan_tat_toan",
    "dropoff_reach_to_register": "ty_le_rot_tiep_can_den_dang_ky",
    "dropoff_register_to_approve": "ty_le_rot_dang_ky_den_duyet",
    "cpl": "chi_phi_moi_lead", "cpa": "chi_phi_moi_don_giai_ngan",
    "cac": "cac_chi_phi_thu_hut_moi_khoan_giai_ngan",
    "avg_loan_value": "gia_tri_khoan_vay_trung_binh",
    "roi_outstanding": "loi_nhuan_lai_tren_du_no",
    "total_journey_hours": "thoi_gian_xu_ly_gio",
    "ad_spend": "chi_phi_lead", "disbursed_amount": "doanh_so_giai_ngan",
    "cost_to_disbursed_ratio": "ty_le_chi_phi_tren_doanh_so",
    "avg_reloan_cadence_days": "chu_ky_vay_lai_ngay",
    "install_count": "so_luot_cai_app", "phone_verified_count": "so_xac_thuc_sdt",
    "avg_lead_to_install_hours": "tb_gio_tu_lead_den_cai_app",
    "avg_install_to_phone_hours": "tb_gio_tu_cai_app_den_xac_thuc_sdt",
    "avg_phone_to_apply_hours": "tb_gio_tu_xac_thuc_sdt_den_dang_ky",
}


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)
    return json.loads(text)


def is_configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY")) and bool(os.environ.get("LLM_MODEL"))


def _pct_diff(value, baseline):
    """% chenh lech so voi muc binh thuong - ngon ngu kinh doanh thay cho
    z-score/do lech chuan. None neu khong tinh duoc (baseline = 0/NaN)."""
    try:
        if baseline is None or value is None:
            return None
        if isinstance(baseline, float) and math.isnan(baseline):
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        if baseline == 0:
            return None
        return round((value - baseline) / baseline * 100, 1)
    except (TypeError, ZeroDivisionError):
        return None


def _channel_comparison(summary_df, anomalies_df) -> dict:
    """Thu hang giua cac kenh + kenh nao CPL tang bat thuong - Python tinh san
    de LLM khong tu suy doan ("cao nhat", "tat ca cac kenh...") va nhan dinh sai."""
    s = summary_df.set_index("campaign_name")
    out = {}
    for col, label in [("cpl", "cpl"), ("cost_to_disbursed_ratio", "ty_le_chi_phi_tren_doanh_so"),
                       ("disbursed_amount", "doanh_so_giai_ngan")]:
        if col in s.columns and s[col].notna().sum() >= 2:
            out[f"kenh_{label}_cao_nhat"] = s[col].idxmax()
            out[f"kenh_{label}_thap_nhat"] = s[col].idxmin()
    flagged = anomalies_df[(anomalies_df["kpi"] == "cpl") & anomalies_df["level"].isin(["warning", "critical"])]
    out["kenh_cpl_tang_bat_thuong"] = flagged["campaign_name"].tolist()
    out["kenh_cpl_binh_thuong"] = [c for c in s.index if c not in set(flagged["campaign_name"])]
    return out


def _contribution_payload(contributions: dict) -> dict:
    """Doi dong gop theo kenh (tu metrics.compute_channel_contributions) sang khoa tieng Viet cho LLM."""
    return {
        c["label"]: {
            "tong_hom_nay": c["tong_hom_nay"], "tong_trung_binh_7_ngay": c["tong_tb_7_ngay"],
            "tong_thay_doi": c["tong_thay_doi"], "phan_tram_thay_doi": c["tong_thay_doi_pct"],
            "theo_kenh": [
                {"kenh": r["campaign_name"], "thay_doi": r["thay_doi"], "ty_trong_dong_gop_pct": r["ty_trong_pct"]}
                for r in c["theo_kenh"]
            ],
        }
        for c in contributions.values()
    }


def build_prompt(as_of_date, product_name, funnel_summary_df, anomalies_df, contributions=None) -> str:
    comparison = _channel_comparison(funnel_summary_df, anomalies_df)
    funnel_summary_df = funnel_summary_df.rename(columns=_FUNNEL_COL_LABELS_VI)
    flagged = anomalies_df[anomalies_df["level"].isin(["warning", "critical"])]
    anomaly_records = []
    for _, r in flagged.iterrows():
        anomaly_records.append({
            "kenh": r["campaign_name"],
            "kpi": r["kpi_label"],
            "loai_kpi": r["kpi_type"],  # "cost" | "conversion" | "volume"
            "gia_tri_hom_nay": r["value"],
            "muc_binh_thuong_7_ngay_gan_nhat": r["baseline_mean"],
            "phan_tram_chenh_lech": _pct_diff(r["value"], r["baseline_mean"]),
            "muc_do": LEVEL_LABEL_VI.get(r["level"], r["level"]),
        })

    payload = {
        "ngay": str(as_of_date),
        "san_pham": product_name,
        "funnel_hom_nay_theo_kenh": funnel_summary_df.to_dict(orient="records"),
        "so_sanh_giua_cac_kenh": comparison,
        "cac_diem_dang_chu_y": anomaly_records,
    }
    if contributions:
        payload["dong_gop_vao_bien_dong_so_voi_tb_7_ngay"] = _contribution_payload(contributions)
    return (
        f"Dữ liệu đã tính sẵn hôm nay cho sản phẩm '{product_name}' (JSON):\n"
        + json.dumps(payload, ensure_ascii=False, default=str, indent=2)
        + "\n\nHãy trả về insight theo ĐÚNG schema JSON sau (giữ nguyên tên field, không thêm field khác):\n"
        + OUTPUT_SCHEMA_HINT
    )


def interpret_daily(as_of_date, product_name, funnel_summary_df, anomalies_df, contributions=None) -> dict:
    """Goi LLM de dien giai RIENG cho 1 san pham (First Loan hoac Re-loan).
    Neu chua cau hinh LLM_API_KEY/LLM_MODEL, tra ve FALLBACK_RESULT thay vi
    loi, de dashboard van chay duoc (chi thieu phan insight)."""
    if not is_configured():
        return dict(FALLBACK_RESULT)

    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )
    model = os.environ["LLM_MODEL"]
    user_prompt = build_prompt(as_of_date, product_name, funnel_summary_df, anomalies_df, contributions)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0.3,
            response_format={"type": "json_object"},
        )
    except Exception:
        # Khong phai provider nao cung ho tro response_format=json_object
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0.3,
        )

    content = response.choices[0].message.content
    try:
        return _extract_json(content)
    except (json.JSONDecodeError, AttributeError):
        result = dict(FALLBACK_RESULT)
        result["tong_quan"] = f"LLM tra ve noi dung khong dung dinh dang JSON. Noi dung tho: {content[:500]}"
        return result
