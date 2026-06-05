import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as app_module
from app import app
from api import devlens_jobs


class FakeTriggerRedis:
    def __init__(self):
        self.lock = None
        self.status = {"status": "idle"}
        self.released = False
        self.settings = Mock(scraper_trigger_stale_after_seconds=900)

    def get_scraper_trigger_lock(self):
        return self.lock

    def acquire_scraper_trigger_lock(self, trigger_id):
        if self.lock:
            return False
        self.lock = trigger_id
        return True

    def release_scraper_trigger_lock(self, trigger_id=None):
        if trigger_id and self.lock and self.lock != trigger_id:
            return False
        self.lock = None
        self.released = True
        return True

    def set_scraper_trigger_status(self, payload):
        self.status = dict(payload)
        return True

    def get_scraper_trigger_status(self):
        return self.status


class TestDevLensJobsPilot(unittest.TestCase):
    def test_roles_endpoint_returns_devlens_roles(self):
        client = TestClient(app)
        response = client.get("/api/roles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["roles"],
            [
                {"key": "backend", "label": "Backend Developer"},
                {"key": "frontend", "label": "Frontend Developer"},
                {"key": "full_stack", "label": "Full Stack Developer"},
                {"key": "mobile", "label": "Mobile Developer"},
                {"key": "ai_ml", "label": "AI/ML Engineer"},
                {"key": "devops", "label": "DevOps Engineer"},
                {"key": "data_engineer", "label": "Data Engineer"},
                {"key": "qa_automation", "label": "QA Automation Engineer"},
            ],
        )

    def test_health_endpoint_returns_ok(self):
        client = TestClient(app)
        response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_run_scraper_starts_background_scrape(self):
        fake_redis = FakeTriggerRedis()
        run_mock = Mock(return_value={"run_id": "run-1", "inserted": 3})

        with patch("app.get_redis_service", return_value=fake_redis), patch(
            "app.run_bulk_pipeline",
            run_mock,
        ), patch(
            "app._start_scraper_thread",
            side_effect=lambda trigger_id: app_module._run_scraper_background(trigger_id),
        ):
            client = TestClient(app)
            response = client.get("/run-scraper")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "started")
        run_mock.assert_called_once()
        self.assertTrue(fake_redis.released)
        self.assertEqual(fake_redis.status["status"], "success")
        self.assertEqual(fake_redis.status["result"]["run_id"], "run-1")

    def test_run_scraper_refuses_duplicate_run(self):
        fake_redis = FakeTriggerRedis()
        fake_redis.lock = "existing-trigger"
        fake_redis.status = {
            "status": "running",
            "trigger_id": "existing-trigger",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch("app.get_redis_service", return_value=fake_redis), patch(
            "app.run_bulk_pipeline",
        ) as run_mock:
            client = TestClient(app)
            response = client.get("/run-scraper")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "already_running")
        self.assertEqual(response.json()["trigger_id"], "existing-trigger")
        run_mock.assert_not_called()

    def test_run_scraper_recovers_stale_running_lock(self):
        fake_redis = FakeTriggerRedis()
        fake_redis.lock = "stale-trigger"
        fake_redis.status = {
            "status": "running",
            "trigger_id": "stale-trigger",
            "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        }
        run_mock = Mock(return_value={"run_id": "run-2", "inserted": 1})

        with patch("app.get_redis_service", return_value=fake_redis), patch(
            "app.run_bulk_pipeline",
            run_mock,
        ), patch(
            "app._start_scraper_thread",
            side_effect=lambda trigger_id: app_module._run_scraper_background(trigger_id),
        ):
            client = TestClient(app)
            response = client.get("/run-scraper")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "started")
        run_mock.assert_called_once()
        self.assertEqual(fake_redis.status["status"], "success")

    def test_scraper_status_returns_current_redis_status(self):
        fake_redis = FakeTriggerRedis()
        fake_redis.status = {"status": "success", "trigger_id": "trigger-1", "result": {"inserted": 4}}

        with patch("app.get_redis_service", return_value=fake_redis):
            client = TestClient(app)
            response = client.get("/scraper-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["result"]["inserted"], 4)

    def test_failed_background_scrape_records_failure_and_releases_lock(self):
        fake_redis = FakeTriggerRedis()

        with patch("app.get_redis_service", return_value=fake_redis), patch(
            "app.run_bulk_pipeline",
            side_effect=RuntimeError("boom"),
        ), patch(
            "app._start_scraper_thread",
            side_effect=lambda trigger_id: app_module._run_scraper_background(trigger_id),
        ):
            client = TestClient(app)
            response = client.get("/run-scraper")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(fake_redis.released)
        self.assertIsNone(fake_redis.lock)
        self.assertEqual(fake_redis.status["status"], "failed")
        self.assertEqual(fake_redis.status["error"], "boom")

    def test_invalid_role_returns_400(self):
        with self.assertRaises(HTTPException) as raised:
            devlens_jobs.build_response_from_cache("data_scientist")

        self.assertEqual(raised.exception.status_code, 400)

    def test_missing_cache_response_does_not_scrape(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = devlens_jobs.build_response_from_cache("backend", cache_dir=Path(tmp))

        self.assertEqual(response["cache_status"], "missing")
        self.assertEqual(response["jobs"], [])
        self.assertEqual(response["count"], 0)

    def test_cache_write_and_read_preserves_normalized_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            payload = {
                "role_key": "backend",
                "role_label": "Backend Developer",
                "queries": ["Backend Developer"],
                "boards": ["indeed"],
                "fetched_at": "2026-06-05T10:00:00+00:00",
                "jobs": [
                    devlens_jobs.normalize_job(
                        {
                            "job_id": "job_1",
                            "title": "Backend Developer",
                            "company": "Acme",
                            "location": "Lahore",
                            "board": "indeed",
                            "job_url": "https://example.test/job-1",
                            "description": "Build APIs with Python and PostgreSQL.",
                            "skills": ["Python", "PostgreSQL"],
                            "employment_type": "Full-time",
                        }
                    )
                ],
                "errors": [],
            }
            devlens_jobs._write_cached_payload(payload, cache_dir)

            response = devlens_jobs.build_response_from_cache("backend", cache_dir=cache_dir)

        self.assertEqual(response["cache_status"], "hit")
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["jobs"][0]["title"], "Backend Developer")
        self.assertEqual(response["jobs"][0]["skills"], ["Python", "PostgreSQL"])

    def test_internship_filter_includes_only_internship_like_jobs(self):
        jobs = [
            devlens_jobs.normalize_job({"title": "Frontend Intern", "description": "React internship"}),
            devlens_jobs.normalize_job({"title": "Frontend Developer", "description": "Build product UI"}),
            devlens_jobs.normalize_job({"title": "QA Trainee", "description": "Learn test automation"}),
        ]

        filtered = devlens_jobs.filter_jobs(jobs, "internships")

        self.assertEqual([job["title"] for job in filtered], ["Frontend Intern", "QA Trainee"])

    def test_jobs_filter_excludes_internship_like_jobs(self):
        jobs = [
            devlens_jobs.normalize_job({"title": "AI Intern", "description": "Model experiments"}),
            devlens_jobs.normalize_job({"title": "AI Engineer", "description": "Deploy ML systems"}),
        ]

        filtered = devlens_jobs.filter_jobs(jobs, "jobs")

        self.assertEqual([job["title"] for job in filtered], ["AI Engineer"])

    def test_refresh_uses_scraper_and_local_cache_without_redis_or_supabase(self):
        fake_jobs = [
            {
                "job_id": "same",
                "title": "Backend Developer",
                "company": "Acme",
                "location": "Remote Pakistan",
                "board": "indeed",
                "job_url": "https://example.test/backend",
                "description": "Build Python APIs.",
                "skills": ["Python"],
                "employment_type": "Full-time",
            },
            {
                "job_id": "intern",
                "title": "Backend Intern",
                "company": "Acme",
                "location": "Lahore",
                "board": "linkedin",
                "job_url": "https://example.test/backend-intern",
                "description": "Internship for API development.",
                "skills": ["Python"],
                "employment_type": "Internship",
            },
        ]

        spider = Mock()
        spider.scrape_all_boards.return_value = fake_jobs

        with tempfile.TemporaryDirectory() as tmp, patch(
            "api.devlens_jobs.JobScraperSpider",
            return_value=spider,
        ), patch("api.devlens_jobs._enrich_jobs", side_effect=lambda jobs, errors: jobs):
            response = devlens_jobs.refresh_role_cache(
                "backend",
                cache_dir=Path(tmp),
                boards=["indeed"],
                max_pages_per_board=1,
                max_jobs_per_board=2,
            )

            cached = devlens_jobs.build_response_from_cache("backend", cache_dir=Path(tmp))

        self.assertEqual(response["cache_status"], "hit")
        self.assertEqual(response["total_cached"], 2)
        self.assertEqual(cached["jobs"][0]["title"], "Backend Developer")
        self.assertGreaterEqual(spider.scrape_all_boards.call_count, 1)


if __name__ == "__main__":
    unittest.main()
