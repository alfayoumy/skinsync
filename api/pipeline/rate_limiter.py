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
from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Optional

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


class LangSearchRateLimiter:
    """
    Inter-Process Atomic Rate Limiter and Token Tracker for LangSearch Web Search API.
    Strictly tracks and enforces:
    1. Daily Token Limit (TPD): 10,000,000 tokens per UTC calendar day.
       Automatically resets daily at 00:00 UTC.
    2. Requests Per Second (RPS): Max 5 RPS (minimum pacing delay >= 0.20s and <= 5 requests per sliding 1s).
    3. Tokens Per Minute (TPM): Max 1,000,000 tokens in a rolling 60-second window.
    4. HTTP 429 Circuit-Breaker: When 100% daily allowance is used or 429 quota exhaustion is detected,
       pauses calls until next 00:00 UTC.
    5. Token Accounting: Extracts input + output tokens from API responses and aggregates in SQLite.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _get_utc_today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_next_utc_reset_info(self) -> Tuple[float, str, float, str]:
        """
        Returns (reset_timestamp, reset_iso, seconds_remaining, human_readable_remaining)
        for the upcoming 00:00 UTC reset.
        """
        now_utc = datetime.now(timezone.utc)
        next_midnight = (now_utc + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        reset_ts = next_midnight.timestamp()
        secs_remaining = max(0.0, reset_ts - now_utc.timestamp())
        hours = int(secs_remaining // 3600)
        minutes = int((secs_remaining % 3600) // 60)
        human_remaining = f"{hours}h {minutes}m"
        return reset_ts, next_midnight.isoformat(), secs_remaining, human_remaining

    def get_quota_status(self) -> Dict[str, Any]:
        """
        Queries persistent database and returns detailed real-time quota status.
        """
        today = self._get_utc_today()
        reset_ts, reset_iso, secs_remaining, human_remaining = self._get_next_utc_reset_info()
        max_daily = cfg.LANGSEARCH_DAILY_TOKEN_LIMIT
        max_rps = cfg.LANGSEARCH_MAX_RPS
        max_tpm = cfg.LANGSEARCH_ROLLING_TPM_LIMIT

        conn = get_connection(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
            SELECT request_count, token_count, is_exhausted, exhausted_until, last_request_ts
            FROM api_rate_limits
            WHERE provider = 'langsearch' AND usage_date = ?;
            """, (today,))
            row = cur.fetchone()
            req_count = row[0] if row else 0
            token_count = row[1] if row else 0
            is_exhausted_flag = bool(row[2]) if row else False
            exhausted_until = row[3] if row else 0.0

            now_ts = time.time()
            # If exhausted_until was set in the past, auto-clear exhaustion flag
            if is_exhausted_flag and exhausted_until > 0 and now_ts >= exhausted_until:
                is_exhausted_flag = False

            # Also check if token_count >= max_daily
            if token_count >= max_daily:
                is_exhausted_flag = True

            # Calculate rolling 60-second token usage
            cutoff_60s = now_ts - 60.0
            cur.execute("""
            SELECT SUM(tokens), COUNT(*)
            FROM api_request_log
            WHERE provider = 'langsearch' AND ts >= ?;
            """, (cutoff_60s,))
            roll_row = cur.fetchone()
            rolling_tokens = (roll_row[0] or 0) if roll_row else 0
            rolling_requests = (roll_row[1] or 0) if roll_row else 0

            used_pct = round(min(100.0, (token_count / max_daily) * 100), 2) if max_daily > 0 else 0.0

            return {
                "provider": "langsearch",
                "usage_date_utc": today,
                "tokens_used_today": token_count,
                "daily_token_limit": max_daily,
                "daily_used_percentage": used_pct,
                "requests_today": req_count,
                "is_daily_exhausted": is_exhausted_flag,
                "resets_in_seconds": int(secs_remaining),
                "resets_in_human": human_remaining,
                "reset_at_utc": reset_iso,
                "rolling_60s_tokens": rolling_tokens,
                "rolling_60s_requests": rolling_requests,
                "max_tpm": max_tpm,
                "max_rps": max_rps,
                "is_allowed_to_call": not is_exhausted_flag and (token_count < max_daily)
            }
        except Exception as e:
            logger.warning(f"Failed to query LangSearch quota status: {e}")
            return {
                "provider": "langsearch",
                "usage_date_utc": today,
                "tokens_used_today": 0,
                "daily_token_limit": max_daily,
                "daily_used_percentage": 0.0,
                "requests_today": 0,
                "is_daily_exhausted": False,
                "resets_in_seconds": int(secs_remaining),
                "resets_in_human": human_remaining,
                "reset_at_utc": reset_iso,
                "rolling_60s_tokens": 0,
                "rolling_60s_requests": 0,
                "max_tpm": max_tpm,
                "max_rps": max_rps,
                "is_allowed_to_call": True
            }
        finally:
            conn.close()

    def is_daily_quota_exhausted(self) -> bool:
        status = self.get_quota_status()
        return status.get("is_daily_exhausted", False)

    def mark_daily_quota_exhausted(self, tokens_used: Optional[int] = None, reason: str = "Daily token allowance exhausted"):
        """
        Marks today's quota as exhausted until 00:00 UTC.
        """
        today = self._get_utc_today()
        reset_ts, _, _, human_remaining = self._get_next_utc_reset_info()
        max_daily = cfg.LANGSEARCH_DAILY_TOKEN_LIMIT
        tokens_val = tokens_used if tokens_used is not None else max_daily

        conn = get_connection(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO api_rate_limits (provider, usage_date, request_count, token_count, is_exhausted, exhausted_until, last_request_ts)
            VALUES ('langsearch', ?, 1, ?, 1, ?, ?)
            ON CONFLICT(provider, usage_date) DO UPDATE SET
                token_count = MAX(token_count, ?),
                is_exhausted = 1,
                exhausted_until = ?;
            """, (today, tokens_val, reset_ts, time.time(), tokens_val, reset_ts))
            conn.commit()
            logger.warning(
                f"LangSearch marked as DAILY EXHAUSTED: {tokens_val:,}/{max_daily:,} tokens on {today}. "
                f"Pausing all calls until 00:00 UTC (Resets in {human_remaining}). Reason: {reason}"
            )
        except Exception as e:
            logger.error(f"Failed to mark LangSearch quota exhausted: {e}")
        finally:
            conn.close()

    def reset_daily_quota(self):
        """
        Manually clears the daily exhaustion flag and resets token counter in SQLite.
        """
        today = self._get_utc_today()
        conn = get_connection(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
            UPDATE api_rate_limits
            SET is_exhausted = 0, exhausted_until = 0.0, token_count = 0
            WHERE provider = 'langsearch' AND usage_date = ?;
            """, (today,))
            conn.commit()
            logger.info(f"LangSearch quota manually reset for {today}.")
        except Exception as e:
            logger.error(f"Failed to reset LangSearch quota in database: {e}")
        finally:
            conn.close()

    def record_usage(self, input_tokens: int = 0, output_tokens: int = 0, total_tokens: Optional[int] = None):
        """
        Records actual token usage from an API response and updates cumulative daily count.
        """
        today = self._get_utc_today()
        now = time.time()
        tokens = total_tokens if total_tokens is not None else max(1, input_tokens + output_tokens)
        max_daily = cfg.LANGSEARCH_DAILY_TOKEN_LIMIT

        conn = get_connection(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO api_rate_limits (provider, usage_date, request_count, token_count, is_exhausted, exhausted_until, last_request_ts)
            VALUES ('langsearch', ?, 1, ?, 0, 0.0, ?)
            ON CONFLICT(provider, usage_date) DO UPDATE SET
                request_count = request_count + 1,
                token_count = token_count + ?,
                last_request_ts = ?;
            """, (today, tokens, now, tokens, now))

            # Also check if token_count has reached max_daily
            cur.execute("SELECT token_count FROM api_rate_limits WHERE provider = 'langsearch' AND usage_date = ?;", (today,))
            r = cur.fetchone()
            curr_tokens = r[0] if r else 0
            if curr_tokens >= max_daily:
                reset_ts, _, _, human_remaining = self._get_next_utc_reset_info()
                cur.execute("""
                UPDATE api_rate_limits
                SET is_exhausted = 1, exhausted_until = ?
                WHERE provider = 'langsearch' AND usage_date = ?;
                """, (reset_ts, today))
                logger.warning(f"LangSearch reached daily allowance: {curr_tokens:,}/{max_daily:,} tokens. Pausing until 00:00 UTC (in {human_remaining}).")

            conn.commit()
        except Exception as e:
            logger.warning(f"Failed to record LangSearch token usage: {e}")
        finally:
            conn.close()

    def record_429_exhaustion(self, response_body: str = "", headers: Optional[dict] = None):
        """
        Processes HTTP 429 response from LangSearch.
        Differentiates between short-term rate limits (RPS/TPM) and daily token allowance exhaustion.
        """
        body_lower = (response_body or "").lower()
        reset_ts, _, _, human_remaining = self._get_next_utc_reset_info()

        # Check if 429 is daily allowance exhaustion
        is_daily = any(phrase in body_lower for phrase in [
            "allowance", "daily", "tokens used today", "100%", "quota", "renew at 00:00", "daily limit"
        ])

        retry_after = 5.0
        if headers:
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra:
                try:
                    retry_after = float(ra)
                    if retry_after > 3600:
                        is_daily = True
                except ValueError:
                    pass

        if is_daily:
            self.mark_daily_quota_exhausted(reason="HTTP 429: Daily token allowance exhausted")
        else:
            logger.warning(f"LangSearch short-term rate limit (RPS/TPM) triggered (Retry-After: {retry_after}s).")
            time.sleep(min(retry_after, 5.0))

    def acquire(self, estimated_tokens: int = 1500) -> bool:
        """
        Acquires permission to send a request to LangSearch.
        Returns True if acquired.
        Returns False immediately if daily quota is exhausted.
        Enforces:
        - Max 5 RPS (minimum pacing delay >= 0.20s and <= 5 requests in sliding 1.0s)
        - Max 1,000,000 TPM (rolling 60s window)
        """
        today = self._get_utc_today()
        max_daily = cfg.LANGSEARCH_DAILY_TOKEN_LIMIT
        max_rps = cfg.LANGSEARCH_MAX_RPS
        max_tpm = cfg.LANGSEARCH_ROLLING_TPM_LIMIT
        pace_delay = cfg.LANGSEARCH_PACE_DELAY_SECONDS

        while True:
            sleep_needed = 0.0
            conn = get_connection(self.db_path)
            try:
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.cursor()

                # 1. Check daily quota & exhaustion flag
                cur.execute("""
                SELECT token_count, is_exhausted, exhausted_until
                FROM api_rate_limits
                WHERE provider = 'langsearch' AND usage_date = ?;
                """, (today,))
                row = cur.fetchone()
                daily_tokens = row[0] if row else 0
                is_exhausted = bool(row[1]) if row else False
                exhausted_until = row[2] if row else 0.0

                now = time.time()
                if is_exhausted and exhausted_until > 0 and now < exhausted_until:
                    conn.rollback()
                    conn.close()
                    logger.debug("LangSearch daily quota exhausted until 00:00 UTC. Skipping.")
                    return False

                if daily_tokens >= max_daily:
                    reset_ts, _, _, human_remaining = self._get_next_utc_reset_info()
                    cur.execute("""
                    UPDATE api_rate_limits
                    SET is_exhausted = 1, exhausted_until = ?
                    WHERE provider = 'langsearch' AND usage_date = ?;
                    """, (reset_ts, today))
                    conn.commit()
                    conn.close()
                    logger.warning(f"LangSearch daily allowance hit ({daily_tokens:,}/{max_daily:,}). Pausing until 00:00 UTC.")
                    return False

                # 2. Prune old request timestamps (> 60 seconds)
                cutoff_60s = now - 60.0
                cur.execute(
                    "DELETE FROM api_request_log WHERE provider = 'langsearch' AND ts < ?;",
                    (cutoff_60s,)
                )

                # 3. Check Rolling 60s Tokens (TPM)
                cur.execute("""
                SELECT SUM(tokens), MIN(ts), MAX(ts), COUNT(*)
                FROM api_request_log
                WHERE provider = 'langsearch' AND ts >= ?;
                """, (cutoff_60s,))
                tpm_row = cur.fetchone()
                rolling_tokens = (tpm_row[0] or 0) if tpm_row else 0
                oldest_60s_ts = tpm_row[1] if tpm_row else None
                newest_ts = tpm_row[2] if tpm_row else None

                if (rolling_tokens + estimated_tokens) > max_tpm and oldest_60s_ts is not None:
                    sleep_needed = max(0.1, 60.0 - (now - oldest_60s_ts) + 0.1)

                # 4. Check RPS (5 requests per second max)
                cutoff_1s = now - 1.0
                cur.execute("""
                SELECT COUNT(*), MIN(ts)
                FROM api_request_log
                WHERE provider = 'langsearch' AND ts >= ?;
                """, (cutoff_1s,))
                rps_row = cur.fetchone()
                reqs_in_1s = rps_row[0] if rps_row else 0
                oldest_1s_ts = rps_row[1] if rps_row else None

                if reqs_in_1s >= max_rps and oldest_1s_ts is not None:
                    sleep_needed = max(sleep_needed, 1.0 - (now - oldest_1s_ts) + 0.05)

                # 5. Check pacing delay
                if newest_ts is not None:
                    elapsed = now - newest_ts
                    if elapsed < pace_delay:
                        sleep_needed = max(sleep_needed, pace_delay - elapsed)

                if sleep_needed > 0.0:
                    conn.rollback()
                    conn.close()
                    time.sleep(min(sleep_needed, 5.0))
                    continue

                # 6. Slot Granted! Atomically record request with estimated tokens
                cur.execute("""
                INSERT INTO api_request_log (provider, ts, tokens)
                VALUES ('langsearch', ?, ?);
                """, (now, estimated_tokens))

                # Touch last_request_ts
                cur.execute("""
                INSERT INTO api_rate_limits (provider, usage_date, request_count, token_count, is_exhausted, exhausted_until, last_request_ts)
                VALUES ('langsearch', ?, 1, 0, 0, 0.0, ?)
                ON CONFLICT(provider, usage_date) DO UPDATE SET
                    last_request_ts = ?;
                """, (today, now, now))

                conn.commit()
                conn.close()
                return True

            except sqlite3.OperationalError:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Error in LangSearchRateLimiter.acquire: {e}")
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
                return False


# Global singleton instances
gemini_limiter = GeminiRateLimiter()
langsearch_limiter = LangSearchRateLimiter()
