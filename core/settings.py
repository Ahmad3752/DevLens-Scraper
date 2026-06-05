"""
Environment-backed settings for the standalone scraper.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from core.devlens_roles import canonical_role_keys, role_label_for_key, filter_role_keys


@dataclass
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    redis_url: str
    redis_max_retries: int
    redis_job_queue_prefix: str
    redis_processed_ttl: int
    scraper_seen_ttl: int
    scraper_stats_ttl: int
    scraper_trigger_lock_ttl_seconds: int
    scraper_trigger_stale_after_seconds: int
    job_scraping_max_pages_per_board: int
    job_scraping_max_jobs_per_board: int
    job_scraping_download_delay: float
    job_scraping_fetch_timeout_ms: int
    permitted_roles: List[str]
    scraper_role_keys: List[str]
    excel_skill_gap: str
    job_stale_after_days: int
    job_scraping_boards: List[str]
    job_scraping_workers: int


_settings: Settings | None = None


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_list(raw: str | None) -> List[str]:
    if not raw:
        return []

    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass

    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_permitted_roles(raw: str | None) -> List[str]:
    parsed = _parse_list(raw)
    if parsed:
        return parsed
    return [role_label_for_key(key) for key in canonical_role_keys()]


def _parse_scraper_role_keys(raw: str | None, permitted_roles: List[str]) -> List[str]:
    parsed = _parse_list(raw)
    keys = filter_role_keys(parsed or permitted_roles)
    return keys or canonical_role_keys()


def _parse_boards(raw: str | None) -> List[str]:
    if not raw or raw.strip().lower() == "all":
        return ["indeed", "linkedin", "rozee", "mustakbil"]
    return [b.strip().lower() for b in raw.split(",") if b.strip()]


def _build_settings() -> Settings:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)

    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        redis_url=os.getenv("SCRAPER_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_max_retries=_get_env_int("REDIS_MAX_RETRIES", 3),
        redis_job_queue_prefix=os.getenv("REDIS_JOB_QUEUE_PREFIX", "jobs"),
        redis_processed_ttl=_get_env_int("REDIS_PROCESSED_TTL", 86400),
        scraper_seen_ttl=_get_env_int("SCRAPER_REDIS_SEEN_TTL", _get_env_int("REDIS_PROCESSED_TTL", 86400)),
        scraper_stats_ttl=_get_env_int("SCRAPER_REDIS_STATS_TTL", 86400),
        scraper_trigger_lock_ttl_seconds=_get_env_int("SCRAPER_TRIGGER_LOCK_TTL_SECONDS", 43200),
        scraper_trigger_stale_after_seconds=_get_env_int("SCRAPER_TRIGGER_STALE_AFTER_SECONDS", 180),
        job_scraping_max_pages_per_board=_get_env_int("JOB_SCRAPING_MAX_PAGES_PER_BOARD", 2),
        job_scraping_max_jobs_per_board=_get_env_int("JOB_SCRAPING_MAX_JOBS_PER_BOARD", 50),
        job_scraping_download_delay=_get_env_float(
            "JOB_SCRAPING_DOWNLOAD_DELAY",
            _get_env_float("DOWNLOAD_DELAY", 2.0),
        ),
        job_scraping_fetch_timeout_ms=_get_env_int("JOB_SCRAPING_FETCH_TIMEOUT_MS", 45000),
        permitted_roles=_parse_permitted_roles(os.getenv("PERMITTED_ROLES")),
        scraper_role_keys=_parse_scraper_role_keys(
            os.getenv("SCRAPER_ROLE_KEYS"),
            _parse_permitted_roles(os.getenv("PERMITTED_ROLES")),
        ),
        excel_skill_gap=os.getenv("EXCEL_SKILL_GAP", "data/skills_master.xlsx"),
        job_stale_after_days=_get_env_int("JOB_STALE_AFTER_DAYS", 7),
        job_scraping_boards=_parse_boards(os.getenv("JOB_SCRAPING_BOARDS")),
        job_scraping_workers=_get_env_int("JOB_SCRAPING_WORKERS", 1),
    )


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings

