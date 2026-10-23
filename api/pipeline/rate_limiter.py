"""
Inter-Process Atomic Rate Limiter for External AI APIs.
Strictly enforces:
1. 15 Requests Per Minute (RPM) sliding window across ALL processes and threads via SQLite.
2. 1,500 Requests Per Day (RPD) persistent counter stored in SQLite WAL database.
3. Non-blocking thread concurrency: sleeping is done outside transaction locks.
"""

import time
import sqlite3
import os
import logging
from datetime import datetime, timezone
from typing import Tuple

from pipeline.config import cfg
from pipeline.db_manager import get_connection, DB_PATH

logger = logging.getLogger("skincare_pipeline.rate_limiter")


class GeminiRateLimiter:
    """
    Enforces Gemini API quotas across multiple processes:
    - Max 15 RPM (Sliding 60-second window + minimum pacing delay via SQLite api_request_log)
    - Max 1,500 RPD (Persisted in SQLite api_rate_limits table via atomic transactions)
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _get_utc_today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def get_daily_usage(self) -> Tuple[int, int]:
        """Returns (current_daily_count, max_rpd)."""
        today = self._get_utc_today()
        try:
            conn = get_connection(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT request_count FROM api_rate_limits WHERE provider = 'gemini' AND usage_date = ?;",
                (today,)
            )
            row = cur.fetchone()
            conn.close()
            count = row[0] if row else 0
            return count, cfg.GEMINI_MAX_RPD
        except Exception as e:
            logger.warning(f"Failed to query daily Gemini usage: {e}")
            return 0, cfg.GEMINI_MAX_RPD

    def is_daily_quota_exhausted(self) -> bool:
        """Checks whether 1,500 RPD limit has been hit."""
        count, max_rpd = self.get_daily_usage()
        return count >= max_rpd

    def acquire(self) -> bool:
        """
        Acquires a request slot, blocking until rate limits are satisfied.
        Fully atomic and safe across multiple processes.
        Returns True if a request slot is acquired.
        Returns False if daily quota (1,500 RPD) is exhausted.
        """
        today = self._get_utc_today()
        max_rpm = cfg.GEMINI_MAX_RPM
        max_rpd = cfg.GEMINI_MAX_RPD
        pace_delay = cfg.GEMINI_PACE_DELAY_SECONDS

        while True:
            sleep_needed = 0.0
            conn = get_connection(self.db_path)
            try:
                # Use BEGIN IMMEDIATE to lock writer across processes atomically
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.cursor()

                # 1. Check daily quota (1,500 RPD)
                cur.execute(
                    "SELECT request_count FROM api_rate_limits WHERE provider = 'gemini' AND usage_date = ?;",
                    (today,)
                )
                row = cur.fetchone()
                daily_count = row[0] if row else 0
                if daily_count >= max_rpd:
                    conn.rollback()
                    conn.close()
                    logger.warning(
                        f"Gemini daily quota reached: {daily_count}/{max_rpd} RPD on {today}. "
                        "Skipping Gemini call to prevent quota exhaustion."
                    )
                    return False

                now = time.time()
                cutoff_60s = now - 60.0

                # 2. Prune old request timestamps (> 60 seconds)
                cur.execute(
                    "DELETE FROM api_request_log WHERE provider = 'gemini' AND ts < ?;",
                    (cutoff_60s,)
                )

                # 3. Query sliding window count and timestamps in last 60 seconds
                cur.execute(
                    "SELECT COUNT(*), MIN(ts), MAX(ts) FROM api_request_log WHERE provider = 'gemini';"
                )
                win_row = cur.fetchone()
                win_count = win_row[0] if win_row else 0
                oldest_ts = win_row[1] if win_row else None
                newest_ts = win_row[2] if win_row else None

                # Check RPM limit
                if win_count >= max_rpm and oldest_ts is not None:
                    sleep_needed = max(0.1, 60.0 - (now - oldest_ts) + 0.1)

                # Check minimum pacing delay between calls
                if sleep_needed == 0.0 and newest_ts is not None:
                    elapsed = now - newest_ts
                    if elapsed < pace_delay:
                        sleep_needed = pace_delay - elapsed

                if sleep_needed > 0.0:
                    # Need to sleep; rollback transaction before sleeping so DB is unlocked
                    conn.rollback()
                    conn.close()
                    time.sleep(sleep_needed)
                    continue  # Re-evaluate in next loop iteration

                # 4. Slot granted! Atomically record timestamp & increment daily counter
                cur.execute(
                    "INSERT INTO api_request_log (provider, ts) VALUES ('gemini', ?);",
                    (now,)
                )
                cur.execute("""
                INSERT INTO api_rate_limits (provider, usage_date, request_count, last_request_ts)
                VALUES ('gemini', ?, 1, ?)
                ON CONFLICT(provider, usage_date) DO UPDATE SET
                    request_count = request_count + 1,
                    last_request_ts = ?;
                """, (today, now, now))

                conn.commit()
                conn.close()
                return True

            except sqlite3.OperationalError as e:
                # Database locked by another process, wait briefly and retry
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
                time.sleep(0.15)
            except Exception as e:
                logger.error(f"Error in GeminiRateLimiter.acquire: {e}")
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
                return False


# Global singleton instance
gemini_limiter = GeminiRateLimiter()
