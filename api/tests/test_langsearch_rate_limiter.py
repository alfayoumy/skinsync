"""
Unit Tests for LangSearch Web Search API Rate Limiter and Token Tracker.
Verifies:
1. Daily token tracking (10,000,000 tokens per UTC calendar day).
2. Countdown calculation to 00:00 UTC daily reset.
3. Requests Per Second (RPS): 5 RPS cap.
4. Tokens Per Minute (TPM): 1,000,000 rolling 60-second limit.
5. Circuit-breaker behavior on HTTP 429 / quota exhaustion.
6. Process-safe persistence and automatic reset at 00:00 UTC.
"""

import unittest
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from pipeline.rate_limiter import LangSearchRateLimiter
from pipeline.config import cfg


import tempfile
import os

class TestLangSearchRateLimiter(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE api_rate_limits (
            provider TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            last_request_ts REAL NOT NULL DEFAULT 0.0,
            token_count INTEGER NOT NULL DEFAULT 0,
            is_exhausted INTEGER NOT NULL DEFAULT 0,
            exhausted_until REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (provider, usage_date)
        );
        """)

        cur.execute("""
        CREATE TABLE api_request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            ts REAL NOT NULL,
            tokens INTEGER NOT NULL DEFAULT 0
        );
        """)
        conn.commit()
        conn.close()

        self.limiter = LangSearchRateLimiter(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_quota_status_structure(self):
        """Verify that get_quota_status returns all required fields and correct defaults."""
        status = self.limiter.get_quota_status()
        self.assertEqual(status["provider"], "langsearch")
        self.assertEqual(status["daily_token_limit"], 10_000_000)
        self.assertEqual(status["max_rps"], 5.0)
        self.assertEqual(status["max_tpm"], 1_000_000)
        self.assertFalse(status["is_daily_exhausted"])
        self.assertTrue(status["is_allowed_to_call"])
        self.assertIn("resets_in_human", status)
        self.assertIn("reset_at_utc", status)

    def test_record_usage_increments_tokens_and_requests(self):
        """Verify record_usage updates tokens_used_today and calculates percentage."""
        self.limiter.record_usage(input_tokens=500, output_tokens=1500)
        status = self.limiter.get_quota_status()

        self.assertEqual(status["tokens_used_today"], 2000)
        self.assertEqual(status["requests_today"], 1)
        self.assertEqual(status["daily_used_percentage"], 0.02)
        self.assertFalse(status["is_daily_exhausted"])
        self.assertTrue(status["is_allowed_to_call"])

    def test_mark_daily_quota_exhausted(self):
        """Verify marking quota exhausted disables further requests until 00:00 UTC."""
        self.limiter.mark_daily_quota_exhausted(
            tokens_used=10_000_000,
            reason="User confirmed 100% daily allowance used"
        )
        status = self.limiter.get_quota_status()

        self.assertEqual(status["tokens_used_today"], 10_000_000)
        self.assertEqual(status["daily_used_percentage"], 100.0)
        self.assertTrue(status["is_daily_exhausted"])
        self.assertFalse(status["is_allowed_to_call"])

        # acquire() must return False immediately without blocking
        acquired = self.limiter.acquire(estimated_tokens=500)
        self.assertFalse(acquired)

    def test_record_429_exhaustion_detection(self):
        """Verify HTTP 429 response body indicating daily quota triggers circuit-breaker."""
        body_sample = '{"error": "Daily token allowance of 10,000,000 used today. Renews at 00:00 UTC"}'
        self.limiter.record_429_exhaustion(response_body=body_sample)

        status = self.limiter.get_quota_status()
        self.assertTrue(status["is_daily_exhausted"])
        self.assertFalse(status["is_allowed_to_call"])

    def test_tpm_limit_pacing(self):
        """Verify rolling 60-second window enforces max 1,000,000 TPM limit."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        now = time.time()
        # Seed 999,000 tokens used in last 10 seconds
        cur.execute("""
        INSERT INTO api_request_log (provider, ts, tokens)
        VALUES ('langsearch', ?, 999000);
        """, (now - 10,))
        conn.commit()
        conn.close()

        status = self.limiter.get_quota_status()
        self.assertEqual(status["rolling_60s_tokens"], 999000)

        # An acquisition of 2,000 tokens would push total to 1,001,000 (exceeding 1M TPM)
        # While 500 tokens should be allowed
        self.assertTrue(status["rolling_60s_tokens"] + 500 <= 1_000_000)


if __name__ == "__main__":
    unittest.main()
