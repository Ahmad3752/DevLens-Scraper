import unittest
from types import SimpleNamespace

from services.redis import RedisService


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.ttls = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.ttls[key] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def exists(self, key):
        return int(key in self.values)

    def delete(self, key):
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        return 1

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hincrby(self, key, field, amount):
        current = int(self.hashes.setdefault(key, {}).get(field, 0))
        current += amount
        self.hashes[key][field] = str(current)
        return current

    def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    def hgetall(self, key):
        return self.hashes.get(key, {}).copy()

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [key for key in self.values if key.startswith(prefix)]


class TestScraperRedis(unittest.TestCase):
    def make_service(self):
        service = RedisService.__new__(RedisService)
        service.settings = SimpleNamespace(
            scraper_seen_ttl=86400,
            scraper_stats_ttl=86400,
            scraper_trigger_lock_ttl_seconds=43200,
            scraper_trigger_stale_after_seconds=180,
            redis_job_queue_prefix="jobs",
            redis_processed_ttl=86400,
        )
        service.client = FakeRedis()
        return service

    def test_reserve_job_uses_seen_key_with_ttl_and_nx(self):
        service = self.make_service()

        self.assertTrue(service.reserve_job("abc"))
        self.assertFalse(service.reserve_job("abc"))
        self.assertTrue(service.is_job_processed("abc"))
        self.assertEqual(service.client.values["scraper:seen:job:abc"], "1")
        self.assertEqual(service.client.ttls["scraper:seen:job:abc"], 86400)

    def test_release_job_reservation_allows_retry(self):
        service = self.make_service()

        service.reserve_job("abc")
        service.release_job_reservation("abc")

        self.assertFalse(service.is_job_processed("abc"))
        self.assertTrue(service.reserve_job("abc"))

    def test_daily_stats_hash_tracks_counts_and_ttl(self):
        service = self.make_service()

        service.increment_scrape_stat("Indeed", "scraped", 2)
        service.increment_scrape_stat("Indeed", "inserted")
        service.increment_scrape_stat("Indeed", "skipped")

        key = "scraper:stats:indeed:today"
        stats = service.get_scrape_stats("indeed")

        self.assertEqual(stats["scraped"], 2)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(service.client.ttls[key], 86400)
        self.assertIn("date", stats)

    def test_scraper_trigger_lock_uses_12_hour_ttl(self):
        service = self.make_service()

        self.assertTrue(service.acquire_scraper_trigger_lock("trigger-1"))
        self.assertFalse(service.acquire_scraper_trigger_lock("trigger-2"))
        self.assertEqual(service.get_scraper_trigger_lock(), "trigger-1")
        self.assertEqual(service.client.ttls["scraper:trigger:lock"], 43200)

    def test_scraper_trigger_status_round_trips_as_json(self):
        service = self.make_service()

        service.set_scraper_trigger_status({"status": "running", "trigger_id": "trigger-1"})
        status = service.get_scraper_trigger_status()

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["trigger_id"], "trigger-1")
        self.assertIn("updated_at", status)

    def test_release_scraper_trigger_lock_preserves_newer_owner(self):
        service = self.make_service()

        service.acquire_scraper_trigger_lock("trigger-1")

        self.assertFalse(service.release_scraper_trigger_lock("trigger-2"))
        self.assertEqual(service.get_scraper_trigger_lock(), "trigger-1")
        self.assertTrue(service.release_scraper_trigger_lock("trigger-1"))
        self.assertIsNone(service.get_scraper_trigger_lock())


if __name__ == "__main__":
    unittest.main()
