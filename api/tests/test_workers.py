"""
Tests for Dedicated Gate 4 Workers:
- Jev Worker (OpenCode Zen / Jev 1.13 Free)
- Gemini Batch Worker (Gemini 3.5 Flash-Lite Multi-Product Batching)
"""

import unittest
from unittest.mock import patch, MagicMock
import sqlite3
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.config import cfg
from pipeline.gemini_worker import chunk_list, run_gemini_verification_queue
from pipeline.jev_worker import run_jev_verification_queue
from pipeline.inci_matcher import verify_batch_with_gemini

class TestWorkerSeparation(unittest.TestCase):

    def test_chunk_list(self):
        items = list(range(25))
        chunks = chunk_list(items, 10)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 10)
        self.assertEqual(len(chunks[1]), 10)
        self.assertEqual(len(chunks[2]), 5)

    @patch("pipeline.inci_matcher.urllib.request.urlopen")
    @patch("pipeline.inci_matcher.gemini_limiter.acquire", return_value=True)
    def test_verify_batch_with_gemini_success(self, mock_acquire, mock_urlopen):
        # Mock Gemini returning JSON array of evaluations
        mock_response_data = [
            {"id": 101, "is_exact_same_formula": True, "confidence": 0.95, "reasoning": "Identical product line"},
            {"id": 102, "is_exact_same_formula": False, "confidence": 0.99, "reasoning": "Sensibio vs Sebium different skin lines"}
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{"text": json.dumps(mock_response_data)}]
                }
            }]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        items = [
            {"id": 101, "store_title": "CeraVe Moisturizing Cream 50ml", "candidate_title": "CeraVe Moisturising Cream"},
            {"id": 102, "store_title": "Bioderma Sensibio H2O", "candidate_title": "Bioderma Sebium H2O"}
        ]

        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key"}):
            results = verify_batch_with_gemini(items)

        self.assertIsNotNone(results)
        self.assertIn(101, results)
        self.assertIn(102, results)
        self.assertTrue(results[101][0])
        self.assertAlmostEqual(results[101][1], 0.95)
        self.assertFalse(results[102][0])
        self.assertAlmostEqual(results[102][1], 0.99)

    @patch("pipeline.inci_matcher.verify_batch_with_gemini")
    @patch("pipeline.gemini_worker.get_connection")
    def test_gemini_worker_batch_execution(self, mock_get_conn, mock_verify_batch):
        # Mock DB records
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur

        mock_cur.fetchall.return_value = [
            {"id": 1, "title": "CeraVe Cream", "brand": "CeraVe", "inci_matched_name": "CeraVe Moisturizing Cream", "inci_hybrid_score": 0.88, "ingredients_raw": "water, glycerin"},
            {"id": 2, "title": "Sensibio H2O", "brand": "Bioderma", "inci_matched_name": "Sebium H2O", "inci_hybrid_score": 0.85, "ingredients_raw": "water, peg-6"}
        ]

        # Mock Gemini batch result
        mock_verify_batch.return_value = {
            1: (True, 0.95, "Identical"),
            2: (False, 0.98, "Different skin targets")
        }

        res = run_gemini_verification_queue(limit=10, batch_size=2)
        self.assertEqual(res["processed"], 2)
        self.assertEqual(res["approved"], 1)
        self.assertEqual(res["rejected"], 1)
        self.assertEqual(res["api_calls"], 1)

    @patch("pipeline.jev_worker.verify_match_with_jev")
    @patch("pipeline.jev_worker.get_connection")
    def test_jev_worker_handoff_to_gemini_on_rate_limit(self, mock_get_conn, mock_verify_jev):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur

        mock_cur.fetchall.return_value = [
            {"id": 5, "title": "Test Prod", "brand": "Test", "inci_matched_name": "Test Ref", "inci_hybrid_score": 0.82, "ingredients_raw": "water"}
        ]

        # Jev is rate limited
        mock_verify_jev.return_value = None

        with patch("pipeline.jev_worker.time.sleep"):
            res = run_jev_verification_queue(limit=1)

        self.assertEqual(res["processed"], 1)
        self.assertEqual(res["rate_limited"], 1)

        # Ensure it handed off to pending_gemini
        sql_calls = [call[0][0] for call in mock_cur.execute.call_args_list]
        handoff_found = any("pending_gemini" in q for q in sql_calls)
        self.assertTrue(handoff_found, "Jev worker did not hand off rate-limited item to pending_gemini")

    def test_jev_worker_stop_event_honored(self):
        import threading
        stop_evt = threading.Event()
        stop_evt.set()  # pre-stopped

        with patch("pipeline.jev_worker.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.cursor.return_value = mock_cur
            mock_cur.fetchall.return_value = [
                {"id": 1, "title": "P1", "brand": "B", "inci_matched_name": "R1", "inci_hybrid_score": 0.9, "ingredients_raw": "water"}
            ]
            res = run_jev_verification_queue(limit=5, stop_event=stop_evt)
            self.assertEqual(res["processed"], 0)

    def test_gemini_worker_stop_event_honored(self):
        import threading
        stop_evt = threading.Event()
        stop_evt.set()  # pre-stopped

        with patch("pipeline.gemini_worker.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_conn.return_value.cursor.return_value = mock_cur
            mock_cur.fetchall.return_value = [
                {"id": 1, "title": "P1", "brand": "B", "inci_matched_name": "R1", "inci_hybrid_score": 0.9, "ingredients_raw": "water"}
            ]
            res = run_gemini_verification_queue(limit=5, stop_event=stop_evt)
            self.assertEqual(res["processed"], 0)

    def test_queue_runner_lifecycle(self):
        from pipeline.queue_runner import SingleQueueRunner
        import time

        runner = SingleQueueRunner("jev")
        self.assertEqual(runner.status, "idle")

        with patch("pipeline.queue_runner.run_jev_verification_queue") as mock_run:
            # Simulate a worker running for a short time
            def slow_worker(**kwargs):
                stop_evt = kwargs.get("stop_event")
                pause_evt = kwargs.get("pause_event")
                cb = kwargs.get("progress_callback")
                if cb:
                    cb({"status": "running", "processed": 1, "total_items": 10, "current_product": "Test Item"})
                for _ in range(20):
                    if stop_evt and stop_evt.is_set():
                        break
                    while pause_evt and pause_evt.is_set():
                        if stop_evt and stop_evt.is_set():
                            break
                        time.sleep(0.05)
                    time.sleep(0.05)
                return {"processed": 1, "approved": 1, "rejected": 0, "rate_limited": 0}

            mock_run.side_effect = slow_worker

            task_id = runner.start({"limit": 10})
            self.assertTrue(runner.is_running)
            self.assertIn("queue_jev_", task_id)

            # Test pause
            paused = runner.pause()
            self.assertTrue(paused)
            self.assertTrue(runner.is_paused)

            # Test resume
            resumed = runner.resume()
            self.assertTrue(resumed)
            self.assertFalse(runner.is_paused)

            # Test stop
            stopped = runner.stop()
            self.assertTrue(stopped)
            runner._thread.join(timeout=2.0)
            self.assertFalse(runner.is_running)

if __name__ == "__main__":
    unittest.main()
