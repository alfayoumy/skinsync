"""Resumable, page-checkpointed existing-offer refresh jobs.

Only invokes the existing offer-only updater. A retry can repeat the last page
after a crash, but never creates new identities/products/INCI, and price/stock
updates are idempotent against the current linked source offer.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from pipeline.db_manager import get_connection
from pipeline.task_tracker import (
    create_task, update_task_progress, complete_task, fail_task,
    get_task_details, log_task_event,
)

LEASE_SECONDS = 600


def now():
    return datetime.now(timezone.utc).isoformat()


def deadline():
    return (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS admin_v2_offer_jobs (
        task_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        start_page INTEGER NOT NULL,
        total_pages INTEGER NOT NULL,
        next_page INTEGER NOT NULL,
        update_price INTEGER NOT NULL,
        update_stock INTEGER NOT NULL,
        dry_run INTEGER NOT NULL,
        desired_action TEXT NOT NULL DEFAULT 'run',
        worker_state TEXT NOT NULL DEFAULT 'queued',
        owner_token TEXT,
        lease_expires_at TEXT,
        updated_at TEXT NOT NULL,
        last_result_json TEXT NOT NULL DEFAULT '{}',
        last_error TEXT
    )""")
    conn.commit()


def state(task_id):
    conn=get_connection()
    try:
        ensure(conn)
        row=conn.execute("SELECT * FROM admin_v2_offer_jobs WHERE task_id=?",(task_id,)).fetchone()
        if not row:
            return None
        value=dict(row)
        value["last_result"]=json.loads(value.pop("last_result_json") or "{}")
        value["effective_state"]=value["worker_state"]
        if (value["worker_state"] in ("queued","running","stopping")
                and value["lease_expires_at"]
                and value["lease_expires_at"] < now()):
            value["effective_state"]="interrupted"
        return value
    finally:
        conn.close()


def start(source_id,start_page,pages,update_price,update_stock,dry_run):
    from routers.workflows import _shopify_source
    if pages<1 or pages>10000 or start_page<1 or not (update_price or update_stock):
        raise HTTPException(422,"Invalid pages, start page, or offer update selection.")
    conn=get_connection()
    try:
        ensure(conn)
        source=_shopify_source(conn,source_id)
        if source["adapter_type"]!="shopify" or source["source_type"]!="retailer":
            raise HTTPException(422,"Only enabled Shopify retailers support tracked offer refresh.")
        blocker=conn.execute("""SELECT task_id FROM admin_v2_offer_jobs
            WHERE worker_state IN ('queued','running','paused','stopping')
              AND (owner_token IS NULL OR lease_expires_at IS NULL OR lease_expires_at>?)
            LIMIT 1""",(now(),)).fetchone()
        if blocker:
            raise HTTPException(409,"Existing offer refresh is active or paused: "+blocker["task_id"])
    finally:
        conn.close()
    task_id=create_task("v2_offer_refresh",target_source=source_id,
        total_items=pages,auto_start=True,exclusive_task_type=True,
        params={"source_id":source_id,"start_page":start_page,"pages":pages,
                "update_price":update_price,"update_stock":update_stock,"dry_run":dry_run})
    conn=get_connection()
    try:
        ensure(conn)
        conn.execute("""INSERT INTO admin_v2_offer_jobs
            (task_id,source_id,start_page,total_pages,next_page,update_price,
             update_stock,dry_run,lease_expires_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (task_id,source_id,start_page,pages,start_page,int(update_price),
             int(update_stock),int(dry_run),deadline(),now()))
        conn.commit()
        return {"task_id":task_id,"status":"queued","source_id":source_id,
                "pages":pages,"dry_run":dry_run,"message":"Offer refresh queued. Checkpoint is saved after every completed page."}
    finally:
        conn.close()


def _finish(task_id,worker_state,status,stage):
    conn=get_connection()
    try:
        conn.execute("""UPDATE admin_v2_offer_jobs SET worker_state=?,
            owner_token=NULL,lease_expires_at=NULL,updated_at=?
            WHERE task_id=?""",(worker_state,now(),task_id))
        conn.execute("""UPDATE pipeline_tasks SET status=?,current_stage=?,
            updated_at=CURRENT_TIMESTAMP,
            completed_at=CASE WHEN ? IN ('cancelled','completed','failed') THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE task_id=?""",(status,stage,status,task_id))
        conn.commit()
    finally:
        conn.close()
    log_task_event(task_id=task_id,message="Offer refresh "+worker_state+"; "+stage)


def worker(task_id):
    """Atomic claim and cooperative boundary controls; safe for explicit restart."""
    from routers.workflows import OfferRefresh, update_existing_shopify_offers, run
    token=uuid.uuid4().hex
    conn=get_connection()
    try:
        ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        job=conn.execute("SELECT * FROM admin_v2_offer_jobs WHERE task_id=?",(task_id,)).fetchone()
        if not job or job["desired_action"]!="run":
            conn.rollback()
            return
        if job["owner_token"] and (not job["lease_expires_at"] or job["lease_expires_at"] > now()):
            conn.rollback()
            return
        conn.execute("""UPDATE admin_v2_offer_jobs SET owner_token=?,
            lease_expires_at=?,worker_state='running',updated_at=?
            WHERE task_id=?""",(token,deadline(),now(),task_id))
        conn.execute("""UPDATE pipeline_tasks SET status='running',
            current_stage='resuming_offer_refresh',updated_at=CURRENT_TIMESTAMP
            WHERE task_id=?""",(task_id,))
        conn.commit()
    finally:
        conn.close()
    try:
        while True:
            conn=get_connection()
            try:
                job=conn.execute("SELECT * FROM admin_v2_offer_jobs WHERE task_id=?",(task_id,)).fetchone()
                if not job or job["owner_token"]!=token:
                    return
                if job["desired_action"]=="pause":
                    _finish(task_id,"paused","paused","paused_before_page_"+str(job["next_page"]))
                    return
                if job["desired_action"]=="stop":
                    _finish(task_id,"stopped","cancelled","stopped_before_page_"+str(job["next_page"]))
                    return
                page=job["next_page"]
                if page>=job["start_page"]+job["total_pages"]:
                    complete_task(task_id,metadata={"last_completed_page":page-1,"next_page":page,
                                                      "offer_only":True,"dry_run":bool(job["dry_run"])})
                    conn.execute("""UPDATE admin_v2_offer_jobs SET worker_state='completed',
                        owner_token=NULL,lease_expires_at=NULL,updated_at=?
                        WHERE task_id=?""",(now(),task_id))
                    conn.commit()
                    return
                conn.execute("""UPDATE admin_v2_offer_jobs SET lease_expires_at=?,updated_at=?
                    WHERE task_id=?""",(deadline(),now(),task_id))
                conn.commit()
                source=job["source_id"]
                update_price=bool(job["update_price"])
                update_stock=bool(job["update_stock"])
                dry_run=bool(job["dry_run"])
                start_page=job["start_page"]
                total_pages=job["total_pages"]
            finally:
                conn.close()
            result=run(update_existing_shopify_offers,OfferRefresh(
                source_id=source,start_page=page,pages=1,update_price=update_price,
                update_stock=update_stock,dry_run=dry_run))
            conn=get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                current=conn.execute("""SELECT owner_token,next_page FROM admin_v2_offer_jobs
                    WHERE task_id=?""",(task_id,)).fetchone()
                if not current or current["owner_token"]!=token:
                    conn.rollback()
                    return
                # A repeated page is harmless: existing keyed variant offers only.
                next_page=max(int(current["next_page"]),page+1)
                conn.execute("""UPDATE admin_v2_offer_jobs SET next_page=?,
                    last_result_json=?,lease_expires_at=?,updated_at=?
                    WHERE task_id=?""",
                    (next_page,json.dumps(result),deadline(),now(),task_id))
                conn.commit()
            finally:
                conn.close()
            done=next_page-start_page
            update_task_progress(task_id,processed_items=done,successful_items=done,
                current_stage="completed_offer_page_"+str(page),
                metadata={"last_completed_page":page,"next_page":next_page,
                          "source_id":source,"last_page_result":result,
                          "dry_run":dry_run,"offer_only":True})
            if not result.get("fetched_product_pages",0):
                # Shopify returned an empty page, no need to request later pages.
                conn=get_connection()
                try:
                    conn.execute("""UPDATE admin_v2_offer_jobs
                        SET next_page=start_page+total_pages WHERE task_id=?""",(task_id,))
                    conn.commit()
                finally:
                    conn.close()
    except Exception as exc:
        conn=get_connection()
        try:
            conn.execute("""UPDATE admin_v2_offer_jobs SET worker_state='failed',
                owner_token=NULL,lease_expires_at=NULL,last_error=?,updated_at=?
                WHERE task_id=?""",(str(exc)[:500],now(),task_id))
            conn.commit()
        finally:
            conn.close()
        fail_task(task_id,"Offer refresh failed; resume from saved last completed page: "+str(exc)[:220])


def command(task_id,action):
    if action not in ("pause","resume","stop"):
        raise HTTPException(422,"Unsupported command.")
    conn=get_connection()
    try:
        ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        job=conn.execute("SELECT * FROM admin_v2_offer_jobs WHERE task_id=?",(task_id,)).fetchone()
        if not job:
            conn.rollback()
            return None
        state=job["worker_state"]
        lease_expired=bool(job["lease_expires_at"] and job["lease_expires_at"]<now())
        if action=="pause" and state=="running" and not lease_expired:
            conn.execute("UPDATE admin_v2_offer_jobs SET desired_action='pause',updated_at=? WHERE task_id=?",(now(),task_id))
            outcome="pause_requested"
        elif action=="stop" and state in ("queued","paused","running","failed"):
            conn.execute("UPDATE admin_v2_offer_jobs SET desired_action='stop',updated_at=? WHERE task_id=?",(now(),task_id))
            outcome="stop_requested"
        elif action=="resume" and (state in ("paused","failed") or lease_expired):
            other=conn.execute("""SELECT task_id FROM admin_v2_offer_jobs
                WHERE task_id!=? AND worker_state IN ('queued','running','paused','stopping')
                  AND (owner_token IS NULL OR lease_expires_at IS NULL OR lease_expires_at>?)
                LIMIT 1""",(task_id,now())).fetchone()
            if other:
                conn.rollback()
                raise HTTPException(409,"Another offer refresh is active: "+other["task_id"])
            conn.execute("""UPDATE admin_v2_offer_jobs SET desired_action='run',
                worker_state='queued',owner_token=NULL,lease_expires_at=NULL,updated_at=?
                WHERE task_id=?""",(now(),task_id))
            outcome="resume_queued"
        else:
            conn.rollback()
            raise HTTPException(409,"Job state does not allow "+action+".")
        conn.commit()
    finally:
        conn.close()
    if action=="stop" and state in ("paused","failed","queued"):
        _finish(task_id,"stopped","cancelled","stopped_at_saved_checkpoint")
    return {"requested":True,"action":action,"status":outcome,"task_id":task_id,
            "message":"Worker checks commands at the next completed page boundary."}
