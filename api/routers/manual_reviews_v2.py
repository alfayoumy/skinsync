"""Unified human pair-review surface; never bypasses identity or formulation gates.

Identity pairs are deliberately restricted to a single, currently evidenced source /
canonical candidate. Ambiguous multi-candidate and canonical-merge decisions remain
in the specialist review tools; one click must never silently decide all candidates.
"""
import hashlib
import json
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal

from pipeline.db_manager import get_connection
from pipeline.review_service import resolve_review
from pipeline.inci_research import ensure_schema as ensure_inci_schema, review_case as review_inci
from pipeline.aggregation_schema import ensure_identity_gemini_schema
from security.admin import ACTOR

router = APIRouter(prefix="/admin/v2/manual-reviews", tags=["Admin V2 manual review"])

def actor_name():
    actor=ACTOR.get()
    if not actor or not actor.get("name"):
        raise HTTPException(401, "An authenticated reviewer is required.")
    return actor["name"]


def _safe_link(value):
    if not isinstance(value, str):
        return None
    try:
        url=urlparse(value.strip())
        if url.scheme in ("http", "https") and url.netloc and not url.username and not url.password:
            return value.strip()
    except ValueError:
        pass
    return None


def _parse(raw, default=None):
    try:
        return json.loads(raw) if raw else (default if default is not None else {})
    except (TypeError, ValueError):
        return default if default is not None else {}


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS admin_v2_manual_review_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_kind TEXT NOT NULL CHECK(queue_kind IN ('identity','inci')),
        case_id INTEGER NOT NULL,
        evidence_fingerprint TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('match','no_match','skip')),
        comment TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_v2_manual_review_events_actor
        ON admin_v2_manual_review_events(reviewer,queue_kind,case_id,action)""")
    conn.commit()


def _image_from_json(raw):
    entries=_parse(raw, [])
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, str) and _safe_link(item):
                return _safe_link(item)
            if isinstance(item, dict):
                for key in ("src", "url", "original", "image_url"):
                    if _safe_link(item.get(key)):
                        return _safe_link(item[key])
    return None


def _source_details(conn, case):
    details=_parse(case["details"])
    obs=None
    observation_id=details.get("observation_id")
    if type(observation_id) is int:
        obs=conn.execute("""SELECT * FROM source_product_observations
            WHERE id=? AND source_id=? AND source_product_id=?""",
            (observation_id,case["source_id"],case["source_product_id"])).fetchone()
    if not obs:
        obs=conn.execute("""SELECT * FROM source_product_observations
            WHERE source_id=? AND source_product_id=?
            ORDER BY id DESC LIMIT 1""",
            (case["source_id"],case["source_product_id"])).fetchone()
    product=None
    product_id=details.get("product_id")
    if type(product_id) is int:
        product=conn.execute("""SELECT * FROM products WHERE id=? AND store_id=?""",
                             (product_id,case["source_id"])).fetchone()
    source=conn.execute("SELECT name,market FROM sources WHERE id=?",(case["source_id"],)).fetchone()
    return details,dict(obs) if obs else None,dict(product) if product else None, dict(source) if source else {}


def _identity_pair(conn, row):
    case=dict(row)
    details,obs,product,source=_source_details(conn,case)
    if not obs and not product:
        return None, "No retained source listing supports this review."
    identity=details.get("identity")
    proposed=identity.get("candidates") if isinstance(identity,dict) else []
    ids={c["canonical_product_id"] for c in proposed
         if isinstance(c,dict) and type(c.get("canonical_product_id")) is int}
    if type(case.get("canonical_product_id")) is int:
        ids.add(case["canonical_product_id"])
    if len(ids)!=1:
        return None, ("Multiple candidate canonicals need an explicit per-candidate workflow."
                      if ids else "No grounded canonical comparator exists.")
    cid=next(iter(ids))
    can=conn.execute("SELECT * FROM canonical_products WHERE id=?",(cid,)).fetchone()
    if not can or can["identity_status"] in ("redirected","reversed_split"):
        return None,"Canonical comparator was removed or superseded."
    can=dict(can)
    offer=conn.execute("""SELECT source_id,title,product_url,image_url,sku,gtin,
        price,currency,normalized_size_ml,normalized_size_g
        FROM product_offers WHERE canonical_product_id=?
        ORDER BY updated_at DESC,id DESC LIMIT 1""",(cid,)).fetchone()
    offer=dict(offer) if offer else None
    old=conn.execute("""SELECT store_id,title,product_url,image_url FROM products
        WHERE canonical_product_id=? ORDER BY id DESC LIMIT 1""",(cid,)).fetchone()
    old=dict(old) if old else {}
    if not offer and not old:
        return None,"Canonical has no independent retailer comparison."
    right_link=_safe_link((offer or {}).get("product_url") or old.get("product_url"))
    left_link=_safe_link((obs or {}).get("source_url") or (product or {}).get("product_url"))
    if not left_link or not right_link:
        return None,"Comparison requires valid source links for both products."
    left_attrs=_parse((obs or {}).get("identity_attributes"))
    right_attrs=_parse(can.get("identity_attributes"))
    left={
        "role":"Retailer listing","title":(obs or {}).get("raw_title") or (product or {}).get("title"),
        "brand":(obs or {}).get("raw_brand") or (product or {}).get("brand"),
        "image":_image_from_json((obs or {}).get("images")) or _safe_link((product or {}).get("image_url")),
        "url":left_link,"retailer":source.get("name") or case["source_id"],
        "market":(obs or {}).get("market") or source.get("market"),
        "sku":(obs or {}).get("manufacturer_sku"),"gtin":(obs or {}).get("source_gtin"),
        "attributes":left_attrs,"ingredients":(obs or {}).get("ingredient_text"),
        "category":(obs or {}).get("raw_category") or (product or {}).get("category"),
    }
    right={
        "role":"Canonical comparison","title":can["canonical_title"],"brand":can["brand"],
        "image":_safe_link(can.get("primary_image_url")) or _safe_link((offer or {}).get("image_url")) or _safe_link(old.get("image_url")),
        "url":right_link,"retailer":(offer or {}).get("source_id") or old.get("store_id"),
        "market":can.get("market"),"sku":(offer or {}).get("sku"),
        "gtin":(offer or {}).get("gtin"),"attributes":right_attrs,
        "ingredients":can.get("ingredients_raw"),"category":can.get("category"),
        "identity_status":can.get("identity_status"),"canonical_id":cid,
        "price":(offer or {}).get("price"),"currency":(offer or {}).get("currency"),
    }
    from pipeline.canonical_identity_resolver import compare_attributes
    conflicts=list(compare_attributes(left_attrs,right_attrs).get("conflicts") or [])
    if left["brand"] and right["brand"] and left["brand"].strip().casefold()!=right["brand"].strip().casefold():
        from pipeline.brand_registry import resolve_brand
        lb=resolve_brand(left["brand"],conn)
        rb=resolve_brand(right["brand"],conn)
        if not lb or not rb or lb["id"]!=rb["id"]:
            conflicts.append("Brand identities disagree.")
    if left.get("gtin") and right.get("gtin") and left["gtin"]!=right["gtin"]:
        conflicts.append("GTIN identifiers disagree.")
    # Current evidence, not old Gemini confidence, determines whether the card is actionable.
    signature={
        "case_id":case["id"],"case_hash":case["evidence_hash"],
        "source_observation_hash":(obs or {}).get("payload_hash"),
        "source_updated_at":(product or {}).get("updated_at"),
        "canonical":{k:can.get(k) for k in ("id","updated_at","identity_attributes","identity_status",
                                           "brand","canonical_title","formula_hash")},
        "comparator":offer or old,
    }
    return {
        "kind":"identity","id":case["id"],"canonical_id":cid,
        "left":left,"right":right,
        "notes":details.get("reason_code") or details.get("reason") or case["kind"],
        "match_blockers":conflicts,
        "model": {"verdict":case.get("verdict"),"confidence":case.get("confidence"),
                  "explanation":case.get("explanation")},
        "fingerprint":hashlib.sha256(json.dumps(signature,sort_keys=True,default=str).encode()).hexdigest()
    },None


def _inci_pair(conn,row):
    q=dict(row)
    can=conn.execute("SELECT * FROM canonical_products WHERE id=?",
                     (q["canonical_product_id"],)).fetchone()
    if not can:
        return None,"Canonical product no longer exists."
    can=dict(can)
    offer=conn.execute("""SELECT product_url,image_url,source_id,title FROM product_offers
        WHERE canonical_product_id=? ORDER BY updated_at DESC,id DESC LIMIT 1""",
        (q["canonical_product_id"],)).fetchone()
    offer=dict(offer) if offer else {}
    source=conn.execute("""SELECT name,market FROM sources
        WHERE lower(domain)=? OR lower(domain)=?""",
        (q["source_domain"],(q["source_domain"] or "").removeprefix("www."))).fetchone()
    source=dict(source) if source else {}
    source_observation=conn.execute("""SELECT images FROM source_product_observations
        WHERE source_url=? ORDER BY id DESC LIMIT 1""",
        (q.get("source_url"),)).fetchone() if q.get("source_url") else None
    source_photo=_image_from_json(source_observation["images"]) if source_observation else None
    left={
        "role":"Canonical product","title":can["canonical_title"],"brand":can["brand"],
        "image":_safe_link(can.get("primary_image_url")) or _safe_link(offer.get("image_url")),
        "url":_safe_link(offer.get("product_url")) or _safe_link(can.get("ingredient_source_url")),
        "retailer":offer.get("source_id"),"market":can.get("market"),
        "attributes":_parse(can.get("identity_attributes")),"category":can.get("category"),
        "ingredients":can.get("ingredients_raw"),"identity_status":can.get("identity_status"),
        "canonical_id":can["id"]
    }
    right={
        "role":"Proposed source INCI","title":q.get("source_title") or can["canonical_title"],
        "brand":can["brand"],"image":source_photo,  # Only the exact source URL's observed image.
        "url":_safe_link(q.get("source_url")),"retailer":source.get("name") or q.get("source_domain"),
        "market":source.get("market"),"attributes":_parse(q.get("source_metadata")),
        "ingredients":q.get("ingredients_raw"),"ingredient_tokens":_parse(q.get("ingredients_tokens"),[]),
        "completeness":q.get("completeness"),"confidence":q.get("confidence"),
        "model_confidence":q.get("model_confidence"),"identity_confidence":q.get("identity_confidence"),
        "source_confidence":q.get("source_confidence"),
        "evidence_excerpt":q.get("evidence_excerpt"),"analysis":_parse(q.get("analysis")),
        "model_name":q.get("model_name"),
    }
    signature={
        "id":q["canonical_product_id"],"identity_fingerprint":q["identity_fingerprint"],
        "status":q["status"],"updated_at":q["updated_at"],"evidence_hash":q.get("evidence_hash"),
        "canonical_updated":can.get("updated_at"),"canonical_status":can.get("identity_status"),
        "canonical_formula":can.get("ingredients_raw"),"canonical_version":can.get("formula_revision"),
    }
    return {"kind":"inci","id":q["canonical_product_id"],"canonical_id":q["canonical_product_id"],
            "left":left,"right":right,"notes":"A complete, exact-market, source-backed formulation is required.",
            "model":{"confidence":q.get("confidence"),"explanation":q.get("analysis")},
            "fingerprint":hashlib.sha256(json.dumps(signature,sort_keys=True,default=str).encode()).hexdigest(),
            "requires_market_confirmation":bool(source.get("market") not in ("EG","MENA"))},None


def _queue(conn,kind):
    if kind=="identity":
        ensure_identity_gemini_schema(conn)
        records=conn.execute("""SELECT r.*,q.status gemini_status,q.verdict,
            q.confidence,q.explanation FROM product_review_queue r
            LEFT JOIN identity_gemini_queue q ON q.review_id=r.id
            WHERE r.status='pending' AND r.source_id IS NOT NULL
              AND r.source_id!='canonical' AND r.kind IN ('identity','duplicate')
              AND (q.review_id IS NULL OR q.status='needs_manual')
            ORDER BY r.updated_at,r.id LIMIT 500""").fetchall()
    else:
        ensure_inci_schema(conn)
        records=conn.execute("""SELECT * FROM inci_research_queue
            WHERE status='needs_review' ORDER BY updated_at,canonical_product_id LIMIT 500""").fetchall()
    return records


def _eligible(conn,kind,actor):
    records=_queue(conn,kind)
    available=[]
    blocked=0
    skipped=0
    for row in records:
        pair,reason=(_identity_pair(conn,row) if kind=="identity" else _inci_pair(conn,row))
        if not pair:
            blocked+=1
            continue
        skip=conn.execute("""SELECT 1 FROM admin_v2_manual_review_events
            WHERE reviewer=? AND queue_kind=? AND case_id=? AND action='skip'
              AND evidence_fingerprint=? LIMIT 1""",
            (actor,kind,pair["id"],pair["fingerprint"])).fetchone()
        if skip:
            skipped+=1
        else:
            available.append(pair)
    return available,blocked,skipped


@router.get("")
def list_manual_reviews(kind:Literal["identity","inci"]=Query("identity"),
                        limit:int=Query(10,ge=1,le=30)):
    actor=actor_name()
    conn=get_connection()
    try:
        _schema(conn)
        available,blocked,skipped=_eligible(conn,kind,actor)
        return {"kind":kind,"reviewer":actor,"items":available[:limit],
                "counts":{"ready":len(available),"advanced":blocked,
                          "skipped_by_you":skipped,"scanned":len(available)+blocked+skipped},
                "scan_limit":500,"partial_scan":len(available)+blocked+skipped>=500}
    finally:
        conn.close()


class Decision(BaseModel):
    action:Literal["match","no_match","skip"]
    fingerprint:str=Field(min_length=64,max_length=64)
    comment:str=Field(default="",max_length=1500)


@router.post("/{kind}/{case_id}")
def decide_manual_review(kind:Literal["identity","inci"],case_id:int,body:Decision):
    reviewer=actor_name()
    conn=get_connection()
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if kind=="identity":
            ensure_identity_gemini_schema(conn)
            row=conn.execute("""SELECT r.*,q.status gemini_status,q.verdict,
                q.confidence,q.explanation FROM product_review_queue r
                LEFT JOIN identity_gemini_queue q ON q.review_id=r.id WHERE r.id=?""",(case_id,)).fetchone()
            if (not row or row["status"]!="pending" or row["source_id"] in (None,"canonical")
                or row["kind"] not in ("identity","duplicate")
                or row["gemini_status"] in ("queued","processing","auto_resolved")):
                raise HTTPException(409,"Identity case is no longer in the manual queue.")
            item,blocker=_identity_pair(conn,row)
        else:
            ensure_inci_schema(conn)
            row=conn.execute("""SELECT * FROM inci_research_queue
                WHERE canonical_product_id=? AND status='needs_review'""",(case_id,)).fetchone()
            if not row:
                raise HTTPException(409,"INCI evidence is no longer awaiting review.")
            item,blocker=_inci_pair(conn,row)
        if not item:
            raise HTTPException(409,blocker or "Evidence is insufficient for quick review.")
        if item["fingerprint"]!=body.fingerprint:
            raise HTTPException(409,"Comparison evidence changed. Reload before deciding.")
        notes=body.comment.strip()
        if body.action=="skip":
            result={"action":"skip","status":"skipped_for_reviewer","no_catalog_mutation":True}
        elif kind=="identity":
            if body.action=="match" and item["match_blockers"]:
                raise HTTPException(409,"Conflicting variant evidence prevents Match: "+", ".join(map(str,item["match_blockers"])))
            reason=notes or ("Reviewed side by side: retailer and canonical identity match."
                             if body.action=="match" else
                             "Reviewed side by side: retailer and canonical identities differ.")
            result=resolve_review(conn,case_id,
                "link" if body.action=="match" else "cannot_link",
                reviewer,reason,item["canonical_id"])
        else:
            if body.action=="match" and item["requires_market_confirmation"]:
                raise HTTPException(409,"Out-of-market INCI needs explicit market verification in advanced review; no implicit override.")
            reason=notes or ("Reviewed complete regional source INCI against the verified canonical product."
                             if body.action=="match" else
                             "Reviewed proposed source INCI; evidence does not match this formulation.")
            result=review_inci(conn,case_id,
                "approve" if body.action=="match" else "reject",
                reviewer,reason,market_compatible=False,correction=False)
        conn.execute("""INSERT INTO admin_v2_manual_review_events
            (queue_kind,case_id,evidence_fingerprint,reviewer,action,comment)
            VALUES (?,?,?,?,?,?)""",
            (kind,case_id,body.fingerprint,reviewer,body.action,notes or None))
        conn.commit()
        return {"kind":kind,"case_id":case_id,"action":body.action,"reviewer":reviewer,
                "result":result,"comment_saved":bool(notes)}
    except HTTPException:
        conn.rollback()
        raise
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(409,str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
