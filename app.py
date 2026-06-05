"""FastAPI entrypoint for the DevLens job-search pilot."""

import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.devlens_jobs import router as devlens_jobs_router
from main import run_bulk_pipeline
from services.redis import get_redis_service


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="DevLens Jobs Pilot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(devlens_jobs_router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale_trigger_lock(redis, payload: dict, active_trigger_id: str) -> bool:
    if payload.get("trigger_id") and payload.get("trigger_id") != active_trigger_id:
        return True
    if payload.get("status") != "running":
        return True

    updated_at = _parse_utc_datetime(str(payload.get("updated_at") or ""))
    if not updated_at:
        return True

    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return age_seconds > redis.settings.scraper_trigger_stale_after_seconds


def _heartbeat_scraper_status(redis, trigger_id: str, started_at: str, stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        if redis.get_scraper_trigger_lock() != trigger_id:
            return
        redis.set_scraper_trigger_status(
            {
                "status": "running",
                "trigger_id": trigger_id,
                "started_at": started_at,
                "heartbeat_at": _utc_now(),
            }
        )


def _run_scraper_background(trigger_id: str) -> None:
    redis = get_redis_service()
    started_at = _utc_now()
    stop_event = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_scraper_status,
        args=(redis, trigger_id, started_at, stop_event),
        daemon=True,
        name=f"scraper-heartbeat-{trigger_id[:8]}",
    )
    heartbeat.start()
    try:
        result = run_bulk_pipeline()
        redis.set_scraper_trigger_status(
            {
                "status": "success",
                "trigger_id": trigger_id,
                "finished_at": _utc_now(),
                "result": result,
            }
        )
    except Exception as exc:
        redis.set_scraper_trigger_status(
            {
                "status": "failed",
                "trigger_id": trigger_id,
                "finished_at": _utc_now(),
                "error": str(exc),
            }
        )
    finally:
        stop_event.set()
        redis.release_scraper_trigger_lock(trigger_id)


def _start_scraper_thread(trigger_id: str) -> None:
    thread = threading.Thread(
        target=_run_scraper_background,
        args=(trigger_id,),
        daemon=True,
        name=f"scraper-trigger-{trigger_id[:8]}",
    )
    thread.start()


@app.get("/run-scraper", status_code=status.HTTP_202_ACCEPTED)
def run_scraper(response: Response):
    try:
        redis = get_redis_service()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}") from exc

    active_trigger_id = redis.get_scraper_trigger_lock()
    if active_trigger_id:
        current_status = redis.get_scraper_trigger_status()
        if _is_stale_trigger_lock(redis, current_status, active_trigger_id):
            redis.release_scraper_trigger_lock(active_trigger_id)
            redis.set_scraper_trigger_status(
                {
                    "status": "failed",
                    "trigger_id": active_trigger_id,
                    "finished_at": _utc_now(),
                    "error": "Recovered stale scraper lock after missing heartbeat",
                    "previous": current_status,
                }
            )
        else:
            response.status_code = status.HTTP_200_OK
            return {
                "status": "already_running",
                "trigger_id": active_trigger_id,
                "current": current_status,
            }

    trigger_id = str(uuid4())
    if not redis.acquire_scraper_trigger_lock(trigger_id):
        response.status_code = status.HTTP_200_OK
        current_status = redis.get_scraper_trigger_status()
        return {
            "status": "already_running",
            "trigger_id": redis.get_scraper_trigger_lock(),
            "current": current_status,
        }

    started_at = _utc_now()
    redis.set_scraper_trigger_status(
        {
            "status": "running",
            "trigger_id": trigger_id,
            "started_at": started_at,
        }
    )
    _start_scraper_thread(trigger_id)

    return {
        "status": "started",
        "trigger_id": trigger_id,
        "started_at": started_at,
    }


@app.get("/scraper-status")
def scraper_status():
    try:
        redis = get_redis_service()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}") from exc

    payload = redis.get_scraper_trigger_status()
    active_trigger_id = redis.get_scraper_trigger_lock()
    if active_trigger_id and payload.get("status") != "running":
        payload = {
            **payload,
            "status": "running",
            "trigger_id": active_trigger_id,
        }
    return payload
