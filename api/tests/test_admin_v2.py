"""Admin V2 workflow persistence and job navigation regression checks."""
from admin_test_support import authenticate
import os
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import app
from pipeline.task_tracker import create_task, complete_task


class AdminV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, headers=authenticate(cls))

    def test_workflow_draft_survives_reload(self):
        r = self.client.post("/admin/v2/workflows", json={
            "kind": "catalog_build", "title": "Regression catalog run"})
        self.assertEqual(r.status_code, 200, r.text)
        wf = r.json()
        self.assertEqual(len(wf["steps"]), 7)
        wf_id = wf["id"]
        s = self.client.put(f"/admin/v2/workflows/{wf_id}/steps/sources", json={
            "configuration": {"source_ids": ["sourcebeauty"], "pages": 0},
            "status": "completed",
            "result": {"selected_sources": ["sourcebeauty"]}
        })
        self.assertEqual(s.status_code, 200, s.text)
        d = self.client.get(f"/admin/v2/workflows/{wf_id}")
        self.assertEqual(d.status_code, 200)
        sources = next(x for x in d.json()["steps"] if x["step_key"] == "sources")
        self.assertEqual(sources["configuration"]["source_ids"], ["sourcebeauty"])
        self.assertEqual(sources["status"], "completed")
        events = self.client.get(f"/admin/v2/workflows/{wf_id}/events").json()["events"]
        self.assertGreaterEqual(len(events), 2)

    def test_unknown_step_and_job_are_rejected(self):
        r = self.client.post("/admin/v2/workflows", json={"kind": "catalog_build"})
        wf_id = r.json()["id"]
        missing = self.client.put(f"/admin/v2/workflows/{wf_id}/steps/imaginary", json={
            "configuration": {}, "status": "draft", "result": {}
        })
        self.assertEqual(missing.status_code, 404)
        fake = self.client.put(f"/admin/v2/workflows/{wf_id}/steps/discovery", json={
            "configuration": {}, "status": "running", "job_id": "nonexistent-job"
        })
        self.assertEqual(fake.status_code, 400)

    def test_job_center_shows_persisted_task_and_rejects_unsupported_pause(self):
        task_id = create_task("admin_v2_test", target_source="test", auto_start=True)
        try:
            jobs = self.client.get("/admin/v2/jobs")
            self.assertEqual(jobs.status_code, 200, jobs.text)
            detail = self.client.get(f"/admin/v2/jobs/{task_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["job"]["task_id"], task_id)
            self.assertEqual(detail.json()["kind"], "pipeline")
            pause = self.client.post(f"/admin/v2/jobs/{task_id}/control",json={"action":"pause"})
            self.assertEqual(pause.status_code,409)
            stop = self.client.post(f"/admin/v2/jobs/{task_id}/control",json={"action":"stop"})
            self.assertEqual(stop.status_code,409)
        finally:
            complete_task(task_id)


    def test_offer_refresh_records_page_checkpoints_without_external_fetch(self):
        from pipeline.db_manager import get_connection
        conn = get_connection()
        try:
            row = conn.execute("""SELECT id FROM sources
                WHERE enabled=1 AND adapter_type='shopify' AND source_type='retailer'
                ORDER BY id LIMIT 1""").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        with patch("routers.workflows.update_existing_shopify_offers",
                   return_value={"fetched_product_pages": 1, "offers_updated": 3}):
            response = self.client.post("/admin/v2/offers/start", json={
                "source_id": row["id"], "start_page": 1,
                "pages": 2, "dry_run": True, "update_price": True, "update_stock": True
            })
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_id"]
        detail = self.client.get(f"/admin/v2/jobs/{task_id}")
        self.assertEqual(detail.status_code, 200)
        task = detail.json()["job"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["processed_items"], 2)
        self.assertEqual(task["metadata"]["offer_job"]["next_page"], 3)
        self.assertEqual(task["metadata"]["offer_job"]["last_result"]["offers_updated"], 3)

    def test_failed_offer_page_can_resume_without_restarting_page_one(self):
        from pipeline.db_manager import get_connection
        from pipeline.v2_offer_jobs import start, worker, state, command
        conn=get_connection()
        try:
            row=conn.execute("""SELECT id FROM sources WHERE enabled=1
                AND adapter_type='shopify' AND source_type='retailer' LIMIT 1""").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        job=start(row["id"],1,2,True,True,True)
        with patch("routers.workflows.update_existing_shopify_offers",
                   side_effect=RuntimeError("temporary network failure")):
            worker(job["task_id"])
        self.assertEqual(state(job["task_id"])["effective_state"],"failed")
        self.assertEqual(state(job["task_id"])["next_page"],1)
        result=command(job["task_id"],"resume")
        self.assertTrue(result["requested"])
        with patch("routers.workflows.update_existing_shopify_offers",
                   return_value={"fetched_product_pages":1,"offers_updated":0}):
            worker(job["task_id"])
        self.assertEqual(state(job["task_id"])["effective_state"],"completed")
        self.assertEqual(state(job["task_id"])["next_page"],3)


    def test_batch_presets_can_be_saved_and_deleted_without_execution(self):
        created=self.client.post("/admin/v2/batch-presets",json={
            "name":"Regression bounded Jev","operation":"jev",
            "limit":20,"batch_size":5})
        self.assertEqual(created.status_code,200,created.text)
        preset_id=created.json()["id"]
        listed=self.client.get("/admin/v2/batch-presets")
        self.assertEqual(listed.status_code,200,listed.text)
        self.assertIn(preset_id,[r["id"] for r in listed.json()["presets"]])
        deleted=self.client.delete("/admin/v2/batch-presets/"+preset_id)
        self.assertEqual(deleted.status_code,200,deleted.text)
        invalid=self.client.post("/admin/v2/batch-presets",json={
            "name":"Invalid","operation":"random","limit":0,"batch_size":999})
        self.assertEqual(invalid.status_code,422)

    def test_operations_overview(self):
        r = self.client.get("/admin/v2/operations")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        for field in ("workflows","recent_jobs","active_pipeline_jobs","inci_workers","waterfall"):
            self.assertIn(field,data)


if __name__ == "__main__":
    unittest.main()
