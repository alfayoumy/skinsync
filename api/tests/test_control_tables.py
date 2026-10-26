"""
Unit and Integration Tests for Control Tables and Task Tracking System:
1. pipeline_tasks (Task lifecycle, status changes, progress tracking, duration)
2. pipeline_subtasks (Subtask lifecycle, granular step tracking)
3. pipeline_task_logs (Structured event logs with levels and context)
4. scrape_checkpoints (Per-store scrape state, pagination cursors, timestamps)
5. REST Endpoints (/tasks, /tasks/{id}, /tasks/{id}/subtasks, /tasks/{id}/logs,
                   /tasks/{id}/cancel, /scraping/checkpoints, /scraping/checkpoints/{store_id})
"""
from admin_test_support import authenticate

import unittest
from unittest.mock import patch
import os
import sys
import json
import time
from fastapi.testclient import TestClient

# Ensure repo/api is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import app
from pipeline.db_manager import get_connection
from pipeline.task_tracker import (
    create_task, start_task, update_task_progress, complete_task,
    fail_task, cancel_task, create_subtask, complete_subtask, fail_subtask,
    log_task_event, get_task_details, list_tasks, get_task_subtasks, get_task_logs,
    get_scrape_checkpoint, list_scrape_checkpoints, update_scrape_checkpoint,
    TaskTracker
)

class TestControlTables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, headers=authenticate(cls))

    # =========================================================================
    # 1. DIRECT TASK TRACKER UNIT TESTS
    # =========================================================================
    def test_task_lifecycle(self):
        """Tests task creation, start, progress update, and completion."""
        task_id = create_task(
            task_type="test_lifecycle",
            target_source="sourcebeauty",
            fields=["price", "stock"],
            params={"batch_size": 25},
            total_items=100,
            auto_start=False
        )
        self.assertIsNotNone(task_id)

        # Check initial state
        task = get_task_details(task_id, include_subtasks=True, include_logs=True)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["task_type"], "test_lifecycle")
        self.assertEqual(task["target_source"], "sourcebeauty")
        self.assertEqual(task["total_items"], 100)
        self.assertEqual(task["progress_percent"], 0.0)

        # Start task
        start_task(task_id)
        task = get_task_details(task_id)
        self.assertEqual(task["status"], "running")
        self.assertIsNotNone(task["started_at"])

        # Update progress
        update_task_progress(
            task_id,
            processed_items=50,
            successful_items=48,
            failed_items=2,
            current_stage="processing_page_2"
        )
        task = get_task_details(task_id)
        self.assertEqual(task["processed_items"], 50)
        self.assertEqual(task["successful_items"], 48)
        self.assertEqual(task["failed_items"], 2)
        self.assertEqual(task["progress_percent"], 50.0)
        self.assertEqual(task["current_stage"], "processing_page_2")

        # Complete task
        res = complete_task(task_id, metadata={"final_report": "OK"})
        self.assertEqual(res["status"], "completed")
        self.assertGreaterEqual(res["duration_seconds"], 0.0)

        task = get_task_details(task_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["progress_percent"], 100.0)
        self.assertIsNotNone(task["completed_at"])

    def test_subtask_and_logs(self):
        """Tests nested subtasks and structured logging under a parent task."""
        task_id = create_task(task_type="test_subtasks", target_source="feel22", total_items=20, auto_start=True)

        # Create subtask
        sub_id = create_subtask(
            task_id=task_id,
            step_name="crawl_page_1",
            step_index=1,
            total_steps=2,
            metadata={"url": "https://feel22.com/page1"}
        )
        self.assertIsNotNone(sub_id)

        # Log event
        log_task_event(
            task_id=task_id,
            subtask_id=sub_id,
            message="Extracted 10 products from page 1",
            level="INFO",
            context_data={"count": 10}
        )

        # Complete subtask
        complete_subtask(subtask_id=sub_id, items_count=10, items_succeeded=10, items_failed=0)

        # Verify via queries
        subtasks = get_task_subtasks(task_id)
        self.assertEqual(len(subtasks), 1)
        self.assertEqual(subtasks[0]["subtask_id"], sub_id)
        self.assertEqual(subtasks[0]["step_name"], "crawl_page_1")
        self.assertEqual(subtasks[0]["status"], "completed")
        self.assertEqual(subtasks[0]["items_count"], 10)

        logs = get_task_logs(task_id)
        self.assertGreaterEqual(len(logs), 2)  # subtask started + manual log + completion log
        found_msg = any("Extracted 10 products" in l["message"] for l in logs)
        self.assertTrue(found_msg)

        complete_task(task_id)

    def test_task_tracker_context_manager(self):
        """Tests the TaskTracker context manager with auto-completion and exception handling."""
        # 1. Normal successful execution
        with TaskTracker(task_type="test_ctx_success", target_source="drdawood", total_items=10) as tracker:
            tracker.update(processed_items=10, successful_items=10)
            tracker.log("Step 1 done")
            succ_id = tracker.task_id

        task = get_task_details(succ_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["processed_items"], 10)

        # 2. Failed execution with exception
        try:
            with TaskTracker(task_type="test_ctx_fail", target_source="drdawood") as tracker:
                fail_id = tracker.task_id
                tracker.log("Starting risky work")
                raise ValueError("Simulated network timeout")
        except ValueError:
            pass

        task_fail = get_task_details(fail_id)
        self.assertEqual(task_fail["status"], "failed")
        self.assertIn("Simulated network timeout", task_fail["error_message"])

    def test_scrape_checkpoints(self):
        """Tests updating, retrieving, and listing store scrape checkpoints."""
        store_id = "sourcebeauty"
        update_scrape_checkpoint(
            store_id=store_id,
            page_scraped=3,
            total_pages=15,
            products_count=75,
            is_success=True,
            status="idle",
            metadata={"last_run": "test"}
        )

        cp = get_scrape_checkpoint(store_id)
        self.assertIsNotNone(cp)
        self.assertEqual(cp["store_id"], store_id)
        self.assertEqual(cp["last_page_scraped"], 3)
        self.assertEqual(cp["total_pages_detected"], 15)
        self.assertGreaterEqual(cp["total_products_scraped"], 75)
        self.assertEqual(cp["status"], "idle")
        self.assertEqual(cp["metadata"].get("last_run"), "test")

        all_cps = list_scrape_checkpoints()
        self.assertGreaterEqual(len(all_cps), 1)
        store_ids = [c["store_id"] for c in all_cps]
        self.assertIn(store_id, store_ids)

    # =========================================================================
    # 2. REST API ENDPOINTS INTEGRATION TESTS
    # =========================================================================
    def test_api_list_tasks(self):
        """Tests GET /tasks with filters and pagination."""
        resp = self.client.get("/tasks?limit=10&offset=0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("tasks", data)
        self.assertIsInstance(data["tasks"], list)

    def test_api_get_task_details(self):
        """Tests GET /tasks/{task_id} and 404 for unknown task."""
        # Create a task to fetch
        t_id = create_task(task_type="api_get_test", target_source="bashrety", auto_start=True)
        log_task_event(t_id, "Testing API get")
        complete_task(t_id)

        resp = self.client.get(f"/tasks/{t_id}?include_subtasks=true&include_logs=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["task_id"], t_id)
        self.assertEqual(data["status"], "completed")
        self.assertIsInstance(data["logs"], list)

        # Unknown task
        resp_404 = self.client.get("/tasks/task_nonexistent_999999")
        self.assertEqual(resp_404.status_code, 404)

    def test_api_get_task_subtasks_and_logs(self):
        """Tests GET /tasks/{task_id}/subtasks and GET /tasks/{task_id}/logs."""
        t_id = create_task(task_type="api_subtask_log_test", auto_start=True)
        s_id = create_subtask(t_id, "api_step_1", 1, 1)
        log_task_event(t_id, "API log event", level="INFO")
        complete_subtask(s_id)
        complete_task(t_id)

        # GET subtasks
        resp_sub = self.client.get(f"/tasks/{t_id}/subtasks")
        self.assertEqual(resp_sub.status_code, 200)
        sub_data = resp_sub.json()
        self.assertEqual(sub_data["task_id"], t_id)
        self.assertGreaterEqual(len(sub_data["subtasks"]), 1)
        self.assertEqual(sub_data["subtasks"][0]["subtask_id"], s_id)

        # GET logs
        resp_log = self.client.get(f"/tasks/{t_id}/logs")
        self.assertEqual(resp_log.status_code, 200)
        log_data = resp_log.json()
        self.assertEqual(log_data["task_id"], t_id)
        self.assertGreaterEqual(len(log_data["logs"]), 1)

    def test_api_cancel_task(self):
        """Tests POST /tasks/{task_id}/cancel for pending/running task and error on already completed."""
        # 1. Create running task and cancel it
        t_id = create_task(task_type="api_cancel_test", auto_start=True)
        resp = self.client.post(f"/tasks/{t_id}/cancel", json={"reason": "Test cancellation via API"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "cancelled")
        self.assertEqual(data["task_id"], t_id)

        # Verify task status in DB
        task = get_task_details(t_id)
        self.assertEqual(task["status"], "cancelled")

        # 2. Try cancelling again -> should fail with 400
        resp_again = self.client.post(f"/tasks/{t_id}/cancel")
        self.assertEqual(resp_again.status_code, 400)
        self.assertIn("already 'cancelled'", resp_again.json()["detail"])

        # 3. Nonexistent task -> 404
        resp_unknown = self.client.post("/tasks/task_fake_id_1234/cancel")
        self.assertEqual(resp_unknown.status_code, 404)

    def test_api_scraping_checkpoints(self):
        """Tests GET /scraping/checkpoints and GET /scraping/checkpoints/{store_id}."""
        # The endpoint test must also pass on a freshly rebuilt catalog, where
        # no historical scrape checkpoint exists and later tests have not run.
        update_scrape_checkpoint('sourcebeauty',page_scraped=1,status='idle')
        # List all checkpoints
        resp = self.client.get("/scraping/checkpoints")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["total"], 1)

        # Get specific store checkpoint
        resp_store = self.client.get("/scraping/checkpoints/sourcebeauty")
        self.assertEqual(resp_store.status_code, 200)
        store_cp = resp_store.json()
        self.assertEqual(store_cp["store_id"], "sourcebeauty")

        # Unknown store checkpoint -> 404
        resp_unknown = self.client.get("/scraping/checkpoints/nonexistent_store_xyz")
        self.assertEqual(resp_unknown.status_code, 404)

    @patch("routers.catalog._run_refresh_catalog_task")
    def test_refresh_dispatch_creates_task(self, worker):
        """Tests that background catalog refresh returns a trackable task_id."""
        resp = self.client.post("/products/refresh", json={
            "store_id": "bashrety",
            "fields": ["price"],
            "max_pages": 1,
            "background": True
        })
        self.assertEqual(resp.status_code, 200)
        worker.assert_called_once()
        data = resp.json()
        self.assertEqual(data["status"], "accepted")
        self.assertIn("task_id", data)

        task_id = data["task_id"]
        # Verify the task was created in pipeline_tasks
        task = get_task_details(task_id, include_subtasks=False, include_logs=False)
        self.assertIsNotNone(task)
        self.assertEqual(task["task_id"], task_id)
        self.assertEqual(task["task_type"], "refresh_catalog")
        self.assertEqual(task["target_source"], "bashrety")

if __name__ == "__main__":
    unittest.main()
