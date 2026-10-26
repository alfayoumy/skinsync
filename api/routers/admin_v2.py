"""SkinSync Admin V2: durable operator journeys and unified read-only operations.

This router deliberately reuses the existing worker endpoints and safety gates.
Workflow state is operational metadata; saving a step NEVER edits catalog data.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from pipeline.db_manager import get_connection
from pipeline.task_tracker import get_task_details, list_tasks

router = APIRouter(prefix="/admin/v2", tags=["Admin V2 operations"])
STEP_KEYS = (
    "sources", "discovery", "identity", "classification", "inci",
    "inci_review", "quality",
)
KINDS = ("catalog_build", "offer_refresh", "catalog_repair")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _decode(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS admin_v2_workflows(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft', current_step TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_v2_steps(
            workflow_id TEXT NOT NULL,
            step_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            config_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            job_id TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(workflow_id, step_key),
            FOREIGN KEY(workflow_id) REFERENCES admin_v2_workflows(id)
        );
        CREATE TABLE IF NOT EXISTS admin_v2_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL, step_key TEXT,
            action TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_v2_workflows_updated
            ON admin_v2_workflows(updated_at);
    """)
    conn.commit()


def _workflow(conn, workflow_id):
    item = conn.execute(
        "SELECT * FROM admin_v2_workflows WHERE id=?", (workflow_id,)
    ).fetchone()
    if not item:
        raise HTTPException(404, "Workflow not found.")
    data = dict(item)
    data["configuration"] = _decode(data.pop("config_json"), {})
    data["steps"] = []
    for row in conn.execute(
        "SELECT * FROM admin_v2_steps WHERE workflow_id=? ORDER BY rowid",
        (workflow_id,),
    ):
        s = dict(row)
        s["configuration"] = _decode(s.pop("config_json"), {})
        s["result"] = _decode(s.pop("result_json"), {})
        s["effective_status"] = s["status"]
        s["job_status"] = None
        if s.get("job_id"):
            task=get_task_details(s["job_id"],include_logs=False,include_subtasks=False)
            if task:
                state=task["status"]
                if task["task_type"]=="v2_offer_refresh":
                    from pipeline.v2_offer_jobs import state as offer_state
                    offer=offer_state(s["job_id"])
                    if offer: state=offer["effective_state"]
                s["job_status"]=state
            elif s["job_id"].startswith("waterfall_"):
                from routers.admin import _waterfall_batch_runner
                water=_waterfall_batch_runner.get_status()
                if water.get("task_id")==s["job_id"]:
                    s["job_status"]=water["status"]
            if s["status"]=="running" and s["job_status"]:
                outcome=s["job_status"]
                if outcome in ("completed","succeeded"):
                    s["effective_status"]="completed"
                elif outcome in ("completed_with_errors","failed","cancelled","stopped","interrupted","paused"):
                    s["effective_status"]=outcome
        data["steps"].append(s)
    return data


def _event(conn, workflow_id, step_key, action, details):
    conn.execute(
        """INSERT INTO admin_v2_events(workflow_id,step_key,action,details_json,created_at)
           VALUES (?,?,?,?,?)""",
        (workflow_id, step_key, action, _json(details), _utc()),
    )


class WorkflowCreate(BaseModel):
    kind: Literal["catalog_build", "offer_refresh", "catalog_repair"] = "catalog_build"
    title: str = Field(default="Catalog build", min_length=1, max_length=120)


class StepSave(BaseModel):
    configuration: dict = Field(default_factory=dict)
    status: Literal["draft", "ready", "previewed", "running", "completed", "needs_review", "blocked", "completed_with_errors"] = "draft"
    result: dict = Field(default_factory=dict)
    job_id: str | None = Field(default=None, max_length=150)


@router.get("/workflows")
def workflows():
    conn = get_connection()
    try:
        _schema(conn)
        rows = conn.execute(
            "SELECT id,kind,title,status,current_step,created_at,updated_at "
            "FROM admin_v2_workflows ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
        return {"workflows": [dict(x) for x in rows]}
    finally:
        conn.close()


@router.post("/workflows")
def create_workflow(req: WorkflowCreate):
    conn = get_connection()
    try:
        _schema(conn)
        workflow_id = "wf_" + uuid.uuid4().hex[:16]
        now = _utc()
        conn.execute(
            """INSERT INTO admin_v2_workflows
               (id,kind,title,status,current_step,created_at,updated_at)
               VALUES (?,?,?,'draft',?,?,?)""",
            (workflow_id, req.kind, req.title, "sources" if req.kind in ("catalog_build", "offer_refresh") else "identity", now, now),
        )
        steps = STEP_KEYS if req.kind == "catalog_build" else (
            ("sources", "discovery", "quality") if req.kind == "offer_refresh"
            else ("identity", "classification", "inci", "inci_review", "quality")
        )
        conn.executemany(
            """INSERT INTO admin_v2_steps(workflow_id,step_key,updated_at)
               VALUES (?,?,?)""", [(workflow_id, step, now) for step in steps]
        )
        _event(conn, workflow_id, None, "created", {"kind": req.kind})
        conn.commit()
        return _workflow(conn, workflow_id)
    finally:
        conn.close()


@router.get("/workflows/{workflow_id}")
def workflow_detail(workflow_id: str):
    conn = get_connection()
    try:
        _schema(conn)
        return _workflow(conn, workflow_id)
    finally:
        conn.close()


@router.put("/workflows/{workflow_id}/steps/{step_key}")
def save_step(workflow_id: str, step_key: str, req: StepSave):
    conn = get_connection()
    try:
        _schema(conn)
        _workflow(conn, workflow_id)
        old = conn.execute(
            "SELECT status,job_id FROM admin_v2_steps WHERE workflow_id=? AND step_key=?",
            (workflow_id, step_key),
        ).fetchone()
        if not old:
            raise HTTPException(404, "Step not part of this workflow.")
        if old["status"] == "running" and req.status == "draft":
            raise HTTPException(409, "This step has an active run; open Job Center before changing it.")
        if req.job_id and not (get_task_details(req.job_id, include_subtasks=False, include_logs=False)
                               or req.job_id.startswith(("waterfall_", "queue_"))):
            raise HTTPException(400, "Referenced job does not exist.")
        now = _utc()
        conn.execute(
            """UPDATE admin_v2_steps SET status=?,config_json=?,result_json=?,
               job_id=?,updated_at=? WHERE workflow_id=? AND step_key=?""",
            (req.status, _json(req.configuration), _json(req.result),
             req.job_id or old["job_id"], now, workflow_id, step_key),
        )
        workflow_status="completed" if step_key=="quality" and req.status=="completed" else "in_progress"
        conn.execute(
            "UPDATE admin_v2_workflows SET current_step=?,updated_at=?,status=? WHERE id=?",
            (step_key, now, workflow_status, workflow_id),
        )
        _event(conn, workflow_id, step_key, "saved", {
            "status": req.status, "job_id": req.job_id, "result_keys": list(req.result)
        })
        conn.commit()
        return _workflow(conn, workflow_id)
    finally:
        conn.close()


@router.get("/workflows/{workflow_id}/events")
def workflow_events(workflow_id: str):
    conn = get_connection()
    try:
        _schema(conn)
        _workflow(conn, workflow_id)
        return {"events": [
            {**dict(r), "details": _decode(r["details_json"], {})}
            for r in conn.execute(
                "SELECT * FROM admin_v2_events WHERE workflow_id=? ORDER BY id DESC LIMIT 300",
                (workflow_id,),
            )
        ]}
    finally:
        conn.close()



class OfferJobStart(BaseModel):
    source_id: str
    start_page: int = Field(default=1, ge=1)
    pages: int = Field(default=2, ge=1, le=10000)
    update_price: bool = True
    update_stock: bool = True
    dry_run: bool = True


@router.post("/offers/start")
def start_offer_job(req: OfferJobStart, background_tasks: BackgroundTasks):
    from pipeline.v2_offer_jobs import start, worker
    result = start(req.source_id, req.start_page, req.pages,
                   req.update_price, req.update_stock, req.dry_run)
    background_tasks.add_task(worker, result["task_id"])
    return result


@router.get("/jobs")
def all_jobs(limit: int = 100):
    """Durable pipeline tasks plus explicitly labelled process-local workers."""
    items = []
    for row in list_tasks(limit=min(200, max(1, limit))):
        entry = dict(row)
        entry["id"] = entry["task_id"]
        entry["kind"] = "pipeline"
        entry["durability"] = "database"
        entry["controls"] = ["stop"] if entry["status"] in ("running", "pending") and entry["task_type"] in (
            "catalog_discovery", "product_identity_gemini", "apply_saved_gemini", "saved_observation_identity_rematch"
        ) else []
        if entry["task_type"] == "v2_offer_refresh":
            from pipeline.v2_offer_jobs import state as offer_state
            offer = offer_state(entry["task_id"])
            if offer:
                entry["offer_job"] = offer
                entry["status"] = offer["effective_state"]
                entry["current_stage"] = "Next offer page " + str(offer["next_page"])
                if entry["status"] == "running":
                    entry["controls"] = ["pause", "stop"]
                elif entry["status"] == "paused":
                    entry["controls"] = ["resume", "stop"]
                elif entry["status"] in ("interrupted", "failed"):
                    entry["controls"] = ["resume"]
                elif entry["status"] == "queued":
                    entry["controls"] = ["stop"]
        items.append(entry)
    from pipeline.queue_runner import master_queue_manager
    from routers.admin import _waterfall_batch_runner
    queue = master_queue_manager.get_status()
    for name in ("jev", "gemini"):
        state = queue[name]
        if state.get("task_id"):
            items.append({
                "id": state["task_id"], "task_id": state["task_id"],
                "task_type": "inci_" + name + "_queue",
                "kind": "process_local", "durability": "process_memory",
                "status": state["status"], "processed_items": state["processed_items"],
                "total_items": state["total_items"],
                "progress_percent": state["progress_percent"],
                "current_stage": state["current_product"],
                "started_at": state["started_at"],
                "controls": (["resume", "stop"] if state["is_paused"] else
                             ["pause", "stop"] if state["is_running"] else []),
            })
    waterfall = _waterfall_batch_runner.get_status()
    if waterfall.get("task_id"):
        items.append({
            "id": waterfall["task_id"], "task_id": waterfall["task_id"],
            "task_type": "inci_waterfall", "kind": "process_local",
            "durability": "process_memory", "status": waterfall["status"],
            "processed_items": waterfall["processed_items"],
            "total_items": waterfall["total_items"],
            "progress_percent": waterfall["progress_percent"],
            "current_stage": waterfall["current_stage"],
            "started_at": waterfall["started_at"],
            "controls": ["stop"] if waterfall["is_running"] else [],
        })
    return {"jobs": items}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str):
    task = get_task_details(job_id)
    if task:
        if task["task_type"] == "v2_offer_refresh":
            from pipeline.v2_offer_jobs import state as offer_state
            offer = offer_state(job_id)
            if offer:
                task["metadata"] = {**(task.get("metadata") or {}), "offer_job": offer}
                task["status"] = offer["effective_state"]
        return {"kind": "pipeline", "job": task}
    from pipeline.queue_runner import master_queue_manager
    from routers.admin import _waterfall_batch_runner
    for name in ("jev", "gemini"):
        state = master_queue_manager.get_status()[name]
        if state.get("task_id") == job_id:
            return {"kind": "process_local", "job": state}
    state = _waterfall_batch_runner.get_status()
    if state.get("task_id") == job_id:
        return {"kind": "process_local", "job": state}
    raise HTTPException(404, "Job not found. Process-local history may be lost after an API restart.")


class JobCommand(BaseModel):
    action: Literal["pause", "resume", "stop"]


@router.post("/jobs/{job_id}/control")
def job_control(job_id: str, cmd: JobCommand, background_tasks: BackgroundTasks):
    """Routes only controls actually supported by the underlying worker."""
    from pipeline.queue_runner import master_queue_manager
    from routers.admin import _waterfall_batch_runner
    for worker in ("jev", "gemini"):
        state = master_queue_manager.get_status()[worker]
        if state.get("task_id") == job_id:
            func = getattr(master_queue_manager, cmd.action)
            return {"requested": func(worker), "action": cmd.action, "job_id": job_id}
    wf = _waterfall_batch_runner.get_status()
    if wf.get("task_id") == job_id:
        if cmd.action != "stop":
            raise HTTPException(409, "Waterfall pause/resume is not durable or supported in this version.")
        return {"requested": _waterfall_batch_runner.cancel(), "action": "stop", "job_id": job_id}
    task = get_task_details(job_id, include_logs=False, include_subtasks=False)
    if not task:
        raise HTTPException(404, "Job not found.")
    if task["task_type"] == "v2_offer_refresh":
        from pipeline.v2_offer_jobs import command, worker
        result = command(job_id, cmd.action)
        if cmd.action == "resume" and result and result["requested"]:
            background_tasks.add_task(worker, job_id)
        return result
    if cmd.action != "stop":
        raise HTTPException(409, "This worker does not support pause/resume. Stop and use its checkpoint to restart.")
    from pipeline.task_tracker import cancel_task
    if task["task_type"] not in (
        "catalog_discovery", "product_identity_gemini",
        "apply_saved_gemini", "saved_observation_identity_rematch",
    ):
        raise HTTPException(409, "This task has no verified cooperative stop adapter.")
    if task["status"] not in ("running", "pending"):
        raise HTTPException(409, "Job is not running.")
    result = cancel_task(job_id, reason="Operator requested stop in Admin V2")
    return {
        "requested": result, "action": "stop", "job_id": job_id,
        "warning": "The legacy task flag changes before worker exit; verify the worker has stopped before maintenance.",
    }


@router.get("/operations")
def operations_overview():
    """One dashboard snapshot without claiming process-memory jobs are durable."""
    from pipeline.queue_runner import master_queue_manager
    from routers.admin import _waterfall_batch_runner
    conn = get_connection()
    try:
        _schema(conn)
        drafts = [dict(row) for row in conn.execute(
            "SELECT id,kind,title,status,current_step,updated_at "
            "FROM admin_v2_workflows ORDER BY updated_at DESC LIMIT 8"
        )]
    finally:
        conn.close()
    tasks = list_tasks(limit=50)
    active = [t for t in tasks if t["status"] in ("running", "pending", "paused")]
    return {
        "workflows": drafts, "active_pipeline_jobs": active,
        "recent_jobs": tasks[:8],
        "inci_workers": master_queue_manager.get_status(),
        "waterfall": _waterfall_batch_runner.get_status(),
        "note": "Legacy INCI queue and waterfall worker state is process-local; restarting the API loses its live progress.",
    }
