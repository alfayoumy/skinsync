"""
Unit and Integration Tests for Centralized Rotational Logging System:
1. Setup and handler configuration (Console, Main Rotating File, Error Rotating File)
2. Size-based rotational rollover and backup count management
3. Dedicated error log isolation (captures only ERROR/CRITICAL)
4. Task context propagation via TaskContextFilter and log_context
5. JSON and standard structured formatting
6. REST API Endpoints (/logs/files, /logs/recent, /logs/rotate)
"""

import unittest
import os
import sys
import shutil
import tempfile
import logging
from fastapi.testclient import TestClient

# Ensure repo/api is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import app
from pipeline.logging_config import (
    setup_logging, get_logger, rotate_logs, get_log_files,
    read_recent_logs, log_context, set_task_context, clear_task_context,
    JsonLogFormatter, TaskContextFilter
)

class TestRotationalLogging(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_logs_")
        self.client = TestClient(app)

    def tearDown(self):
        clear_task_context()
        # Clean up handlers attached to root logger before removing directory
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                root.removeHandler(h)
                h.close()
            except Exception:
                pass
        shutil.rmtree(self.test_dir, ignore_errors=True)
        # Restore default app logging
        setup_logging(force_reconfigure=True)

    # =========================================================================
    # 1. CORE LOGGING SETUP & HANDLERS
    # =========================================================================
    def test_setup_logging_creates_files(self):
        """Verifies that setup_logging initializes main and error log files in target directory."""
        handlers = setup_logging(
            log_dir=self.test_dir,
            log_file_name="test_app.log",
            log_error_file_name="test_error.log",
            to_console=True,
            to_file=True,
            force_reconfigure=True
        )
        self.assertIn("file_main", handlers)
        self.assertIn("file_error", handlers)
        self.assertIn("console", handlers)

        test_logger = get_logger("test.component")
        test_logger.info("Informational message 123")
        test_logger.error("Critical failure alert 456")

        main_path = os.path.join(self.test_dir, "test_app.log")
        error_path = os.path.join(self.test_dir, "test_error.log")

        self.assertTrue(os.path.exists(main_path))
        self.assertTrue(os.path.exists(error_path))

        with open(main_path, "r", encoding="utf-8") as f:
            main_content = f.read()
        with open(error_path, "r", encoding="utf-8") as f:
            error_content = f.read()

        # Main log receives both INFO and ERROR
        self.assertIn("Informational message 123", main_content)
        self.assertIn("Critical failure alert 456", main_content)

        # Error log receives only ERROR
        self.assertNotIn("Informational message 123", error_content)
        self.assertIn("Critical failure alert 456", error_content)

    # =========================================================================
    # 2. ROTATIONAL ROLLOVER
    # =========================================================================
    def test_size_based_rotation(self):
        """Verifies that log files rotate automatically when max_bytes threshold is exceeded."""
        # Use tiny max_bytes (500 bytes) to trigger immediate rotation
        setup_logging(
            log_dir=self.test_dir,
            log_file_name="rotate.log",
            log_error_file_name="rotate_err.log",
            max_bytes=500,
            backup_count=3,
            to_console=False,
            to_file=True,
            force_reconfigure=True
        )

        test_logger = get_logger("test.rotator")
        # Write ~25 lines of 50 chars each -> ~1250 bytes > 500 bytes
        for i in range(25):
            test_logger.info(f"Iteration {i:02d}: " + "X" * 40)

        files = os.listdir(self.test_dir)
        # Should have rotate.log and at least rotate.log.1
        rotated_files = [f for f in files if f.startswith("rotate.log")]
        self.assertGreaterEqual(len(rotated_files), 2)
        self.assertIn("rotate.log", files)
        self.assertTrue(any(".log." in f for f in rotated_files))

    def test_manual_log_rotation(self):
        """Verifies that rotate_logs() forces immediate rollover of active handlers."""
        setup_logging(
            log_dir=self.test_dir,
            log_file_name="manual.log",
            log_error_file_name="manual_err.log",
            to_file=True,
            force_reconfigure=True
        )
        test_logger = get_logger("test.manual")
        test_logger.info("Message before manual rotation")

        res = rotate_logs()
        self.assertEqual(res["status"], "success")
        self.assertIn("file_main", res["rotated_handlers"])

        test_logger.info("Message after manual rotation")

        files = os.listdir(self.test_dir)
        self.assertIn("manual.log", files)
        self.assertIn("manual.log.1", files)

    # =========================================================================
    # 3. TASK CONTEXT INJECTION
    # =========================================================================
    def test_task_context_filter(self):
        """Verifies that TaskContextFilter injects task_id and subtask_id into log records."""
        setup_logging(
            log_dir=self.test_dir,
            log_file_name="context.log",
            log_error_file_name="context_err.log",
            to_console=False,
            to_file=True,
            force_reconfigure=True
        )
        test_logger = get_logger("test.context")

        # Outside context -> no prefix
        test_logger.info("Normal message")

        # Inside context -> includes task and subtask prefix
        with log_context(task_id="task_abc_123", subtask_id="sub_xyz_789"):
            test_logger.info("Task tracked message")

        # Context cleared
        test_logger.info("Another normal message")

        with open(os.path.join(self.test_dir, "context.log"), "r", encoding="utf-8") as f:
            lines = [l for l in f.readlines() if "Centralized rotational logging initialized" not in l]

        self.assertIn("Normal message", lines[0])
        self.assertNotIn("[task_abc_123", lines[0])

        self.assertIn("[task_abc_123:sub_xyz_789]", lines[1])
        self.assertIn("Task tracked message", lines[1])

        self.assertIn("Another normal message", lines[2])
        self.assertNotIn("[task_abc_123", lines[2])

    # =========================================================================
    # 4. JSON FORMATTING
    # =========================================================================
    def test_json_formatter(self):
        """Verifies that JsonLogFormatter produces valid JSON with required metadata fields."""
        setup_logging(
            log_dir=self.test_dir,
            log_file_name="json.log",
            log_error_file_name="json_err.log",
            json_format=True,
            to_console=False,
            to_file=True,
            force_reconfigure=True
        )
        test_logger = get_logger("test.json")

        with log_context(task_id="task_json_456"):
            test_logger.warning("JSON warning event")

        records = read_recent_logs(file_name="json.log", log_dir=self.test_dir)
        self.assertGreaterEqual(len(records), 1)

        rec = records[0]
        self.assertEqual(rec["level"], "WARNING")
        self.assertEqual(rec["logger"], "test.json")
        self.assertEqual(rec["message"], "JSON warning event")
        self.assertEqual(rec["task_id"], "task_json_456")
        self.assertIn("timestamp", rec)
        self.assertIn("process", rec)

    # =========================================================================
    # 5. REST API ENDPOINTS
    # =========================================================================
    def test_api_log_endpoints(self):
        """Verifies /logs/files, /logs/recent, and /logs/rotate REST endpoints."""
        # Restore app logging to ensure API uses its configured directory
        setup_logging(force_reconfigure=True)
        api_logger = get_logger("test.api")
        api_logger.info("API integration test log entry")

        # 1. GET /logs/files
        resp_files = self.client.get("/logs/files")
        self.assertEqual(resp_files.status_code, 200)
        data_files = resp_files.json()
        self.assertEqual(data_files["status"], "success")
        self.assertIsInstance(data_files["files"], list)
        self.assertGreaterEqual(len(data_files["files"]), 1)

        # 2. GET /logs/recent
        resp_recent = self.client.get("/logs/recent?limit=50")
        self.assertEqual(resp_recent.status_code, 200)
        data_recent = resp_recent.json()
        self.assertEqual(data_recent["status"], "success")
        self.assertIsInstance(data_recent["logs"], list)
        self.assertGreaterEqual(len(data_recent["logs"]), 1)

        # 3. POST /logs/rotate
        resp_rotate = self.client.post("/logs/rotate")
        self.assertEqual(resp_rotate.status_code, 200)
        data_rotate = resp_rotate.json()
        self.assertEqual(data_rotate["status"], "success")
        self.assertIn("rotated_handlers", data_rotate)

if __name__ == "__main__":
    unittest.main()
