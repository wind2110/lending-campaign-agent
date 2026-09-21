from dotenv import load_dotenv

load_dotenv()

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.chat import ChatError, answer_question
from app.pipeline import DashboardUnavailable, ensure_dashboard_cache, is_running, run_daily_pipeline
from app.scheduler import start_scheduler
from app.security import (
    BASE_HEADERS, CSP_FOR_API, BodyLimitMiddleware, RefreshGuard, client_ip, csp_for_html, new_nonce,
)

logger = logging.getLogger("lending-campaign-agent")

# Tat trang tai lieu tu dong (/docs, /redoc, /openapi.json): khong can cho nguoi dung cuoi va
# la ban do chi duong cho ke tan cong.
app = FastAPI(title="lending-campaign-agent", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BodyLimitMiddleware)
refresh_guard = RefreshGuard()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in BASE_HEADERS.items():
        response.headers.setdefault(name, value)
    response.headers.setdefault("Content-Security-Policy", CSP_FOR_API)  # trang HTML tu dat CSP rieng (co nonce)
    return response


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Dashboard storytelling day du. Neu chua co ban cache (lan chay dau tien), tu chay pipeline
    1 lan de co du lieu hien thi ngay (nhieu nguoi vao cung luc chi chay 1 lan)."""
    try:
        html = ensure_dashboard_cache()
    except DashboardUnavailable:
        return HTMLResponse("Dashboard đang được chuẩn bị. Vui lòng thử lại sau ít phút.", status_code=503)
    except Exception:
        logger.exception("Khong dung duoc Dashboard")
        return HTMLResponse("Chưa thể dựng Dashboard lúc này. Vui lòng thử lại sau.", status_code=503)
    # CSP theo nonce: moi lan tra ve 1 nonce moi, chi the <script> mang nonce do moi duoc chay.
    nonce = new_nonce()
    html = html.replace("<script", f'<script nonce="{nonce}"')
    return HTMLResponse(html, headers={"Content-Security-Policy": csp_for_html(nonce)})


@app.post("/api/refresh")
def refresh(request: Request):
    """Chay thu cong toan bo pipeline: doc du lieu -> tinh KPI/bat thuong -> LLM dien giai -> build
    dashboard -> gui email. Job tu dong hang ngay (app/scheduler.py) cung goi ham nay.
    Can header X-Refresh-Token = bien moi truong REFRESH_TOKEN (xem app/security.py)."""
    denied = refresh_guard.check(request)
    if denied is not None:
        return denied
    if is_running() or not refresh_guard.running.acquire(blocking=False):
        return JSONResponse({"error": "Agent đang chạy một lượt phân tích khác. Vui lòng đợi hoàn tất."}, status_code=409)
    try:
        result = run_daily_pipeline()
    except Exception:
        logger.exception("Loi khi chay pipeline thu cong")
        return JSONResponse({"error": "Chạy phân tích thất bại. Xem nhật ký của agent để biết chi tiết."}, status_code=500)
    finally:
        refresh_guard.running.release()
        refresh_guard.mark_finished()
    return JSONResponse(result)


class AskRequest(BaseModel):
    question: str = Field(max_length=2000)   # gioi han nghiep vu (600) kiem tra trong chat.py de tra cau thong bao than thien
    product: str = Field(default="", max_length=50)
    history: list[dict] = Field(default_factory=list, max_length=20)


@app.post("/api/ask")
def ask(body: AskRequest, request: Request):
    """Hoi dap ve so lieu/phan tich tren Dashboard. Chi tra ve cau tra loi dang text; moi loi tra
    ve {"error": "<cau tieng Viet hien thang cho nguoi dung>"} - khong bao gio kem chi tiet noi bo."""
    try:
        answer = answer_question(body.question, body.product, body.history, client_ip(request))
    except ChatError as e:
        return JSONResponse({"error": e.public_message}, status_code=e.status)
    except Exception:
        logger.exception("Loi khi tra loi cau hoi")
        return JSONResponse({"error": "Không thể trả lời lúc này. Vui lòng thử lại sau."}, status_code=500)
    return {"answer": answer}


start_scheduler()

if __name__ == "__main__":
    import uvicorn

    # server_header=False: khong lo ten/phien ban may chu. proxy_headers=False: tu xu ly
    # X-Forwarded-For (app/security.py::client_ip) thay vi de uvicorn tin mu.
    uvicorn.run(app, host="0.0.0.0", port=8080, server_header=False, proxy_headers=False)  # nosec B104 - container phai lang nghe moi giao dien
