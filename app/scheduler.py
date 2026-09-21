"""Job tu dong chay hang ngay luc DAILY_CUTOFF_HOUR (mac dinh 8h sang,
gio Viet Nam) - tinh so lieu "hom qua" (0h-24h) khi da du lieu day du.

Chi chay 1 job trong-process (APScheduler) - CHU Y: runtime phai duoc gioi
han min=max=1 replica khi deploy, vi khong co co che khoa phan tan (distributed
lock) de tranh chay trung neu co >1 replica.
"""
from __future__ import annotations

import os

from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from app.pipeline import run_daily_pipeline

    hour = int(os.environ.get("DAILY_CUTOFF_HOUR", "8"))
    _scheduler = BackgroundScheduler(timezone="Asia/Ho_Chi_Minh")
    _scheduler.add_job(
        run_daily_pipeline, "cron", hour=hour, minute=0,
        id="daily_pipeline", replace_existing=True,
    )
    _scheduler.start()
    return _scheduler
