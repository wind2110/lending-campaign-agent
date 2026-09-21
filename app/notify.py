"""Gui tom tat hang ngay. Hien tai: Email qua Gmail SMTP.
Zalo OA bi hoan sang giai doan sau (chua co Zalo OA + access token) - xem
send_zalo_critical_alert() ben duoi, chua duoc goi o dau trong pipeline.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def is_email_configured() -> bool:
    return bool(os.environ.get("EMAIL_SENDER")) and bool(os.environ.get("EMAIL_APP_PASSWORD")) and bool(
        os.environ.get("EMAIL_RECIPIENTS")
    )


def build_email_html(as_of_date, llm_result: dict, dashboard_url: str) -> str:
    critical = llm_result.get("critical_highlights") or []
    if critical:
        items = "".join(
            f"<li style='margin-bottom:10px;'><strong>{c.get('campaign_name', '')}</strong>: "
            f"{c.get('insight_1_cau', '')}<br>"
            f"<span style='color:#52514e;'>Đề xuất: {c.get('de_xuat_1_cau', '')}</span></li>"
            for c in critical
        )
        critical_html = (
            "<h3 style='color:#d03b3b;margin-bottom:8px;'>Cần lưu ý</h3>"
            f"<ul style='padding-left:18px;'>{items}</ul>"
        )
    else:
        critical_html = "<p>Không có campaign nào ở mức cần lưu ý hôm nay.</p>"

    overview = llm_result.get("tong_quan") or "Chưa có insight (LLM chưa được cấu hình)."

    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color:#0b0b0b;">
      <h2>Tóm tắt Campaign Lending - {as_of_date}</h2>
      <p>{overview}</p>
      {critical_html}
      <p style="margin-top: 24px;">
        <a href="{dashboard_url}" style="background:#2a78d6;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;">
          Xem dashboard đầy đủ
        </a>
      </p>
      <p style="color:#898781;font-size:12px;margin-top:24px;">
        AI Agent chỉ phân tích và đề xuất - mọi hành động do con người quyết định và thực thi.
      </p>
    </div>
    """


def send_daily_email(as_of_date, llm_result: dict, dashboard_url: str) -> bool:
    """Gui email tom tat. Tra ve False neu chua cau hinh (khong loi)."""
    if not is_email_configured():
        return False

    sender = os.environ["EMAIL_SENDER"]
    app_password = os.environ["EMAIL_APP_PASSWORD"]
    recipients = [r.strip() for r in os.environ["EMAIL_RECIPIENTS"].split(",") if r.strip()]
    if not recipients:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Lending Campaign] Tom tat ngay {as_of_date}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(build_email_html(as_of_date, llm_result, dashboard_url), "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        server.sendmail(sender, recipients, msg.as_string())
    return True


def send_zalo_critical_alert(*args, **kwargs):
    """Chua trien khai - de sau khi co Zalo Official Account (OA) + access token.
    Se goi Zalo OA Send API, chi gui cac campaign muc Critical + 1 cau insight +
    1 cau de xuat (khong lap lai toan bo dashboard), theo dung Muc 6 cua plan.
    """
    raise NotImplementedError("Zalo notification chua duoc cau hinh trong phase nay.")
