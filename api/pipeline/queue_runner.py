"""
Queue Runner and Lifecycle Manager for Gate 4 Jev and Gemini Workers.
Provides thread-safe start, pause, resume, stop, and status tracking for LLM verification queues.
"""

import threading
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from typing import Dict, Any, Optional

from pipeline.db_manager import get_connection
from pipeline.jev_worker import run_jev_verification_queue
from pipeline.gemini_worker import run_gemini_verification_queue
from pipeline.logging_config import get_logger

logger = get_logger("skincare_pipeline.queue_runner")


class SingleQueueRunner:
    def __init__(self, name: str):
        self.name = name  # "jev" or "gemini"
        self._lock = threading.Lock()
        self.is_running = False
        self.is_paused = False
        self.status = "idle"  # "idle", "running", "paused", "completed", "stopped", "error"
        self.task_id: Optional[str] = None
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.duration_seconds: float = 0.0
        self.total_items: int = 0
        self.processed_items: int = 0
        self.progress_percent: float = 0.0
        self.current_product: str = "Idle"
        self.stats: Dict[str, Any] = {
            "processed": 0,
            "approved": 0,
            "rejected": 0,
            "rate_limited": 0,
            "api_calls": 0
        }
        self.recent_logs = deque(maxlen=60)
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _add_log(self, msg: str):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.recent_logs.append(f"[{now_str}] {msg}")

    def start(self, config: Optional[Dict[str, Any]] = None) -> str:
        config = config or {}
        with self._lock:
            if self.is_running:
                raise RuntimeError(f"{self.name.capitalize()} worker is already running (Task ID: {self.task_id}).")

            task_id = f"queue_{self.name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
            self.task_id = task_id
            self.is_running = True
            self.is_paused = False
            self.status = "running"
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.completed_at = None
            self.duration_seconds = 0.0
            self.total_items = 0
            self.processed_items = 0
            self.progress_percent = 0.0
            self.current_product = f"Initializing {self.name.upper()} queue..."
            self.stats = {"processed": 0, "approved": 0, "rejected": 0, "rate_limited": 0, "api_calls": 0}
            self.recent_logs.clear()
            self.stop_event.clear()
            self.pause_event.clear()

            self._add_log(f"Started {self.name.upper()} queue worker (Task ID: {task_id})")

            self._thread = threading.Thread(target=self._run_worker_thread, args=(config,), daemon=True)
            self._thread.start()
            return task_id

    def pause(self) -> bool:
        with self._lock:
            if not self.is_running or self.is_paused:
                return False
            self.is_paused = True
            self.status = "paused"
            self.pause_event.set()
            self._add_log(f"Worker {self.name.upper()} paused by user request.")
            return True

    def resume(self) -> bool:
        with self._lock:
            if not self.is_running or not self.is_paused:
                return False
            self.is_paused = False
            self.status = "running"
            self.pause_event.clear()
            self._add_log(f"Worker {self.name.upper()} resumed by user request.")
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running:
                return False
            self.stop_event.set()
            self.pause_event.clear()  # Clear pause so loop can exit immediately
            self.status = "stopping"
            self._add_log(f"Worker {self.name.upper()} stop requested. Terminating current loop...")
            return True

    def _progress_callback(self, data: Dict[str, Any]):
        with self._lock:
            if "status" in data and not self.stop_event.is_set():
                if self.is_paused:
                    self.status = "paused"
                else:
                    self.status = data.get("status", self.status)

            if "current_product" in data:
                self.current_product = data["current_product"]

            if "total_items" in data:
                self.total_items = data["total_items"]

            if "processed" in data:
                self.processed_items = data["processed"]
                if self.total_items > 0:
                    self.progress_percent = round((self.processed_items / self.total_items) * 100.0, 1)

            for k in ["approved", "rejected", "rate_limited", "api_calls"]:
                if k in data:
                    self.stats[k] = data[k]

    def _log_callback(self, log_msg: str):
        self._add_log(log_msg)

    def _run_worker_thread(self, config: Dict[str, Any]):
        limit = config.get("limit")
        try:
            if self.name == "jev":
                res = run_jev_verification_queue(
                    limit=limit,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                    progress_callback=self._progress_callback,
                    log_callback=self._log_callback
                )
            else:
                batch_size = config.get("batch_size")
                all_pending = config.get("all_pending", False)
                res = run_gemini_verification_queue(
                    limit=limit,
                    batch_size=batch_size,
                    all_pending=all_pending,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                    progress_callback=self._progress_callback,
                    log_callback=self._log_callback
                )

            with self._lock:
                self.is_running = False
                self.is_paused = False
                self.completed_at = datetime.now(timezone.utc).isoformat()
                if self.stop_event.is_set():
                    self.status = "stopped"
                    self.current_product = f"Stopped. Processed {self.processed_items}/{self.total_items} items."
                    self._add_log(f"Worker {self.name.upper()} stopped by user.")
                else:
                    self.status = "completed"
                    self.progress_percent = 100.0
                    self.current_product = f"Run complete. Processed {self.processed_items} items."
                    self._add_log(f"Worker {self.name.upper()} finished all items successfully.")

                if isinstance(res, dict):
                    for k in ["approved", "rejected", "rate_limited", "api_calls", "processed"]:
                        if k in res:
                            self.stats[k] = res[k]
        except Exception as e:
            logger.error(f"Error in {self.name} queue runner thread: {e}", exc_info=True)
            with self._lock:
                self.is_running = False
                self.is_paused = False
                self.status = "error"
                self.completed_at = datetime.now(timezone.utc).isoformat()
                self.current_product = f"Error: {str(e)[:80]}"
                self._add_log(f"Error in {self.name.upper()}: {str(e)}")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            duration = self.duration_seconds
            if self.started_at:
                try:
                    st = datetime.fromisoformat(self.started_at)
                    et = datetime.fromisoformat(self.completed_at) if self.completed_at else datetime.now(timezone.utc)
                    duration = round(max(0.0, (et - st).total_seconds()), 1)
                except Exception:
                    pass

            return {
                "name": self.name,
                "is_running": self.is_running,
                "is_paused": self.is_paused,
                "status": self.status,
                "task_id": self.task_id,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "duration_seconds": duration,
                "total_items": self.total_items,
                "processed_items": self.processed_items,
                "progress_percent": self.progress_percent,
                "current_product": self.current_product,
                "stats": dict(self.stats),
                "recent_logs": list(self.recent_logs)
            }


class MasterQueueManager:
    def __init__(self):
        self.jev = SingleQueueRunner("jev")
        self.gemini = SingleQueueRunner("gemini")
        self._both_thread: Optional[threading.Thread] = None

    def get_db_metrics(self) -> Dict[str, Any]:
        """Queries database for live counts of queue depth and verified items."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            # Jev counts
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'pending_jev';")
            jev_pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'jev_approved';")
            jev_approved = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'jev_rejected';")
            jev_rejected = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_jev_status = 'jev_rate_limited';")
            jev_rate_limited = cur.fetchone()[0]

            # Gemini counts
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'pending_gemini';")
            gemini_pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'gemini_approved';")
            gemini_approved = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE gate4_gemini_status = 'gemini_rejected';")
            gemini_rejected = cur.fetchone()[0]

            return {
                "jev": {
                    "pending": jev_pending,
                    "approved": jev_approved,
                    "rejected": jev_rejected,
                    "rate_limited": jev_rate_limited
                },
                "gemini": {
                    "pending": gemini_pending,
                    "approved": gemini_approved,
                    "rejected": gemini_rejected
                }
            }
        finally:
            conn.close()

    def get_status(self) -> Dict[str, Any]:
        jev_status = self.jev.get_status()
        gemini_status = self.gemini.get_status()
        db_metrics = self.get_db_metrics()

        jev_status["queue_depth"] = db_metrics["jev"]
        gemini_status["queue_depth"] = db_metrics["gemini"]

        return {
            "jev": jev_status,
            "gemini": gemini_status,
            "is_any_running": jev_status["is_running"] or gemini_status["is_running"],
            "is_any_paused": jev_status["is_paused"] or gemini_status["is_paused"]
        }

    def start(self, worker: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        config = config or {}
        worker = worker.lower().strip()
        if worker == "jev":
            task_id = self.jev.start(config)
            return {"worker": "jev", "task_id": task_id, "message": "Jev verification queue started."}
        elif worker == "gemini":
            task_id = self.gemini.start(config)
            return {"worker": "gemini", "task_id": task_id, "message": "Gemini batch verification queue started."}
        elif worker == "both":
            # Run sequentially: Jev first, then Gemini
            def _run_both():
                try:
                    self.jev.start(config)
                    if self.jev._thread:
                        self.jev._thread.join()
                except Exception as e:
                    logger.error(f"Error running Jev in combined queue: {e}")

                try:
                    self.gemini.start(config)
                except Exception as e:
                    logger.error(f"Error running Gemini in combined queue: {e}")

            self._both_thread = threading.Thread(target=_run_both, daemon=True)
            self._both_thread.start()
            return {"worker": "both", "message": "Sequential Jev + Gemini verification run started."}
        else:
            raise ValueError(f"Unknown worker: {worker}. Must be 'jev', 'gemini', or 'both'.")

    def pause(self, worker: str = "all") -> Dict[str, bool]:
        worker = worker.lower().strip()
        results = {}
        if worker in ("jev", "all"):
            results["jev"] = self.jev.pause()
        if worker in ("gemini", "all"):
            results["gemini"] = self.gemini.pause()
        return results

    def resume(self, worker: str = "all") -> Dict[str, bool]:
        worker = worker.lower().strip()
        results = {}
        if worker in ("jev", "all"):
            results["jev"] = self.jev.resume()
        if worker in ("gemini", "all"):
            results["gemini"] = self.gemini.resume()
        return results

    def stop(self, worker: str = "all") -> Dict[str, bool]:
        worker = worker.lower().strip()
        results = {}
        if worker in ("jev", "all"):
            results["jev"] = self.jev.stop()
        if worker in ("gemini", "all"):
            results["gemini"] = self.gemini.stop()
        return results


master_queue_manager = MasterQueueManager()
