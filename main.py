from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from app.pipeline import run_daily_pipeline, DASHBOARD_CACHE_PATH
from app.scheduler import start_scheduler

app = FastAPI(title="lending-campaign-agent")


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Dashboard storytelling day du. Neu chua co ban cache (lan chay dau
    tien), tu chay pipeline 1 lan de co du lieu hien thi ngay."""
    if not DASHBOARD_CACHE_PATH.exists():
        run_daily_pipeline()
    return DASHBOARD_CACHE_PATH.read_text(encoding="utf-8")


@app.post("/api/refresh")
def refresh():
    """Chay thu cong toan bo pipeline: doc du lieu -> tinh KPI/bat thuong ->
    LLM dien giai -> build dashboard -> gui email. Job tu dong hang ngay
    (app/scheduler.py) cung goi ham nay."""
    result = run_daily_pipeline()
    return JSONResponse(result)


start_scheduler()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
