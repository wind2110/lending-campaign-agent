"""Cac lop bao ve dung chung cho server (header bao mat, gioi han body, xac thuc /api/refresh,
xac dinh IP nguoi dung).

Muc tieu: dashboard va /api/ask la cong khai (ai co link deu vao duoc), nen moi thu co the ton
tien (goi LLM, chay lai toan bo pipeline) phai duoc chan lam dung, va trinh duyet phai duoc
"khoa" bang CSP de ngay ca khi co loi tiem ma thi ma doc cung khong chay duoc.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(32 * 1024)))

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # Dashboard chua so lieu kinh doanh: khong de trinh duyet/proxy dung chung luu lai
    "Cache-Control": "no-store",
}


def new_nonce() -> str:
    return secrets.token_urlsafe(16)


def csp_for_html(nonce: str) -> str:
    """CSP cho trang Dashboard: chi chay script co nonce cua CHINH lan phan hoi nay (+ Chart.js tu
    jsDelivr, co kem SRI trong the <script>); khong cho ket noi/nhung di noi nao khac."""
    return "; ".join([
        "default-src 'self'",
        f"script-src 'nonce-{nonce}' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline'",   # Dashboard dung style="..." inline; script moi la thu quan trong
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])


CSP_FOR_API = "default-src 'none'; frame-ancestors 'none'"


# ---------------------------------------------------------------- gioi han kich thuoc body
class BodyLimitMiddleware:
    """Tu choi som (413) request qua lon - kiem tra ca Content-Length lan tong so byte thuc nhan
    (phong truong hop gui kieu chunked khong khai bao do dai)."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def reject():
            resp = JSONResponse({"error": "Yêu cầu quá lớn."}, status_code=413)
            await resp(scope, receive, send)

        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            return await reject()

        received, started = 0, False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await reject()


class _BodyTooLarge(Exception):
    pass


# ---------------------------------------------------------------- IP nguoi dung
def client_ip(request: Request) -> str:
    """IP dung de gioi han tan suat. KHONG tin phan dau cua X-Forwarded-For: nguoi dung tu them
    duoc gia tri tuy y vao do (de 'doi IP' moi lan hoi). Proxy tin cay (cong cua nen tang) them IP
    that vao CUOI danh sach, nen lay phan tu thu TRUSTED_PROXY_HOPS tinh tu cuoi
    (mac dinh 1 proxy). Neu khong hop le/khong co thi dung dia chi ket noi truc tiep."""
    peer = request.client.host if request.client else "unknown"
    try:
        hops = max(int(os.environ.get("TRUSTED_PROXY_HOPS", "1")), 0)
    except ValueError:
        hops = 1
    if hops == 0:
        return peer
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(parts) >= hops:
        candidate = parts[-hops]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer


# ---------------------------------------------------------------- bao ve /api/refresh
class RefreshGuard:
    """/api/refresh chay lai toan bo pipeline (~2 phut, ton phi LLM) nen:
    - bat buoc REFRESH_TOKEN (header X-Refresh-Token), so sanh thoi gian hang dinh; chua cau hinh
      token thi TU CHOI (dong theo mac dinh, khong bao gio mo toang);
    - chan doan mo token (qua nhieu lan sai theo IP);
    - moi luc chi 1 lan chay, va cach nhau toi thieu REFRESH_MIN_INTERVAL_SECONDS."""

    MAX_BAD_PER_HOUR = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._bad: dict[str, deque] = defaultdict(deque)
        self._last_finished = 0.0
        self.running = threading.Lock()

    def check(self, request: Request) -> JSONResponse | None:
        """Tra ve response loi neu khong duoc phep, None neu hop le."""
        expected = os.environ.get("REFRESH_TOKEN", "")
        if not expected:
            return JSONResponse({"error": "Chức năng làm mới thủ công đang tắt (chưa cấu hình REFRESH_TOKEN)."}, status_code=503)
        ip, now = client_ip(request), time.time()
        with self._lock:
            q = self._bad[ip]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.MAX_BAD_PER_HOUR:
                return JSONResponse({"error": "Quá nhiều lần thử sai. Vui lòng thử lại sau."}, status_code=429)
            supplied = request.headers.get("x-refresh-token", "")
            if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
                q.append(now)
                return JSONResponse({"error": "Không có quyền."}, status_code=401)
            wait = float(os.environ.get("REFRESH_MIN_INTERVAL_SECONDS", "300")) - (now - self._last_finished)
            if wait > 0:
                return JSONResponse({"error": f"Vừa làm mới xong. Vui lòng thử lại sau {int(wait) + 1} giây."},
                                    status_code=429, headers={"Retry-After": str(int(wait) + 1)})
        return None

    def mark_finished(self) -> None:
        with self._lock:
            self._last_finished = time.time()
