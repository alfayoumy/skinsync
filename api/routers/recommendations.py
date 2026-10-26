"""
Recommendations Router: User onboarding, product search, cosmetic recommendations,
database catalog stats, background workers, and ingredient synonyms.
"""

import os
import re
import sqlite3
from typing import List, Union, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from simple_db import db
from pipeline.db_manager import load_synonyms_from_db, add_synonym_to_db
from pipeline.logging_config import get_logger
from services.catalog_service import (
    DB_PATH, get_catalog_df, load_catalog_from_db, get_synonyms, set_synonyms
)
from services.recommender import get_recommendations

logger = get_logger("skincare_engine.routers.recommendations")

router = APIRouter(tags=["recommendations"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class OnboardingRequest(BaseModel):
    name: str
    email: str
    gender: str
    age_group: str
    likes: Union[List[str], str]
    dislikes: Union[List[str], str]

class ProductRequest(BaseModel):
    product_title: str
    user_skin_type: str = "normal"
    category_mode: Optional[str] = "same_category"
    target_category: Optional[str] = None

class SynonymRequest(BaseModel):
    alias: str
    canonical_name: str
    category: Optional[str] = "general"

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.get("/")
@router.get("/health")
def health_check():
    """Health check endpoint reporting active database mode and indexed products count."""
    df = get_catalog_df()
    return {
        "status": "active",
        "database": "Production SQLite WAL",
        "products_indexed": len(df),
        "products_with_ingredients": int(df['ingredients'].fillna('').astype(str).str.strip().ne('').sum()),
        "ai_engine": "Jev 1.13 Free (OpenCode Zen) + Cosmetic Formulation Bioequivalence"
    }

@router.post("/onboarding")
def save_onboarding(data: OnboardingRequest):
    """Saves or updates user onboarding profile in simple_db."""
    try:
        user_data = data.dict() if hasattr(data, "dict") else data.model_dump()
        email = user_data['email'].lower().strip()
        user_data['email'] = email
        existing = db.find_one("users", {"email": email})
        if existing:
            db.update("users", {"email": email}, user_data)
            return {"status": "updated", "user_id": existing['_id']}
        else:
            user_id = db.insert("users", user_data)
            return {"status": "created", "user_id": user_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user")
def get_user(email: str):
    """Retrieves user onboarding profile by email."""
    user = db.find_one("users", {"email": email.lower().strip()})
    if user:
        return user
    raise HTTPException(status_code=404, detail="User not found")

@router.post("/recommend")
def recommend(request: ProductRequest):
    """Finds cosmetic alternatives and formulation dupes for a source product."""
    data = get_recommendations(
        target_title=request.product_title,
        skin_type=request.user_skin_type,
        category_mode=request.category_mode or "same_category",
        target_category=request.target_category
    )
    if not data:
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "user_skin": request.user_skin_type,
        "source_product": data['source'],
        "results": data['results']
    }

@router.get("/search")
def search_products(query: str):
    """Typeahead search returning product title suggestions."""
    if not query or len(query.strip()) < 2:
        return []
    q = query.strip().lower()
    df = get_catalog_df()
    prefix_matches = df[df['title'].str.lower().str.startswith(q, na=False)]['title'].tolist()
    contains_matches = df[df['title'].str.contains(re.escape(q), case=False, na=False)]['title'].tolist()
    combined = []
    seen = set()
    for title in prefix_matches + contains_matches:
        if title not in seen:
            seen.add(title)
            combined.append(title)
        if len(combined) >= 8:
            break
    return combined

@router.get("/stats")
def catalog_stats():
    """Returns live database metrics, store product counts, and price change tracking."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM products;")
    total_products = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM price_history;")
    total_price_changes = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM inci_reference;")
    inci_records = cur.fetchone()[0]

    cur.execute("SELECT id, name, domain, total_products, last_sync_at FROM stores;")
    stores = [dict(zip(["id", "name", "domain", "total_products", "last_sync_at"], r)) for r in cur.fetchall()]

    cur.execute("""
    SELECT store_id, status, products_fetched, products_added, products_updated, price_changes, completed_at
    FROM sync_runs ORDER BY id DESC LIMIT 5;
    """)
    recent_syncs = [dict(zip(["store_id", "status", "fetched", "added", "updated", "price_changes", "completed_at"], r)) for r in cur.fetchall()]
    conn.close()

    return {
        "database": "SQLite (WAL Mode)",
        "total_active_products": total_products,
        "inci_knowledge_records": inci_records,
        "total_price_changes_logged": total_price_changes,
        "stores": stores,
        "recent_sync_runs": recent_syncs
    }

# ==============================================================================
# BACKGROUND ADMIN WORKER ENDPOINTS
# ==============================================================================

def background_sync_task():
    try:
        from pipeline.daily_sync import run_daily_sync
        logger.info("Executing background daily sync...")
        run_daily_sync(max_pages_per_store=50)
        load_catalog_from_db()
        logger.info("Background daily sync finished and memory cache reloaded.")
    except Exception as e:
        logger.error(f"Background sync error: {e}")

@router.post("/admin/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """Triggers an automated background daily synchronization across all Shopify sources."""
    background_tasks.add_task(background_sync_task)
    return {
        "status": "accepted",
        "message": "Daily synchronization job dispatched to background worker. Check /stats for progress."
    }

def background_jev_task(limit: int):
    try:
        from pipeline.jev_worker import run_jev_verification_queue
        run_jev_verification_queue(limit=limit)
        load_catalog_from_db()
    except Exception as e:
        logger.error(f"Background Jev task error: {e}")

@router.post("/admin/verify/jev")
def trigger_jev_verification(background_tasks: BackgroundTasks, limit: int = 25):
    """Triggers asynchronous Gate 4 Jev verification queue."""
    background_tasks.add_task(background_jev_task, limit)
    return {
        "status": "accepted",
        "message": f"Gate 4 Jev verification job dispatched for up to {limit} products. Check /stats for updates."
    }

def background_gemini_task(limit: int, batch_size: int, all_pending: bool):
    try:
        from pipeline.gemini_worker import run_gemini_verification_queue
        run_gemini_verification_queue(limit=limit, batch_size=batch_size, all_pending=all_pending)
        load_catalog_from_db()
    except Exception as e:
        logger.error(f"Background Gemini task error: {e}")

@router.post("/admin/verify/gemini")
def trigger_gemini_verification(background_tasks: BackgroundTasks, limit: int = 50, batch_size: int = 10, all_pending: bool = False):
    """Triggers asynchronous Gate 4 Gemini batch verification queue."""
    background_tasks.add_task(background_gemini_task, limit, batch_size, all_pending)
    return {
        "status": "accepted",
        "message": f"Gate 4 Gemini batch verification job dispatched for up to {limit} products (batch size: {batch_size}). Check /stats for updates."
    }

# ==============================================================================
# SYNONYMS ENDPOINTS
# ==============================================================================

@router.get("/synonyms")
def list_synonyms():
    """Returns all active ingredient synonyms from the database."""
    return load_synonyms_from_db()

@router.post("/synonyms")
def create_or_update_synonym(req: SynonymRequest):
    """Dynamically adds or updates an ingredient synonym in the database and updates cache."""
    ok = add_synonym_to_db(req.alias, req.canonical_name, req.category)
    if ok:
        new_synonyms = load_synonyms_from_db()
        set_synonyms(new_synonyms)
        try:
            from pipeline.inci_matcher import reload_synonyms
            reload_synonyms()
        except ImportError:
            pass
        try:
            from dermo_data import reload_dermo_lookup_cache
            reload_dermo_lookup_cache()
        except ImportError:
            pass
        return {
            "status": "success",
            "alias": req.alias.lower().strip(),
            "canonical_name": req.canonical_name.strip(),
            "category": req.category
        }
    raise HTTPException(status_code=500, detail="Failed to persist synonym in database.")
