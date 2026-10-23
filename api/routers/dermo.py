"""
Dermatology Router: Clinical comedogenicity (acne risk 0-10) and irritancy scoring (0-10)
and clinical ingredient database management.
"""

import json
from typing import List, Optional, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dermo_data import evaluate_product_dermo, reload_clinical_db
from pipeline.db_manager import (
    get_connection, load_clinical_ingredients_from_db, add_clinical_ingredient_to_db
)
from pipeline.logging_config import get_logger

logger = get_logger("skincare_engine.routers.dermo")

router = APIRouter(tags=["dermo"])

# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class DermoScoreRequest(BaseModel):
    ingredients: Union[List[str], str]
    skin_type: str = "acne_prone"  # oily, dry, sensitive, acne_prone, normal

class ClinicalIngredientCreateRequest(BaseModel):
    inci: str
    common_name: Optional[str] = None
    functional_type: str = "active"
    comedogenicity: int = 0
    irritancy: int = 0
    tags: Optional[List[str]] = None
    evidence: Optional[str] = None
    contraindications: Optional[List[str]] = None
    notes: Optional[str] = None

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.post("/dermo/score")
def score_dermo_endpoint(req: DermoScoreRequest):
    """
    Evaluates clinical comedogenicity (acne risk 0-10) and irritancy risk (0-10)
    for a list or string of ingredients based on the database-backed clinical ingredient catalog.
    """
    return evaluate_product_dermo(req.ingredients, skin_type=req.skin_type)

@router.get("/products/{product_id}/dermo-score")
def score_product_dermo_endpoint(product_id: int, skin_type: str = "acne_prone"):
    """
    Retrieves a product from the database catalog and computes its full clinical dermatological score.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, brand, ingredients_raw, ingredients_parsed FROM products WHERE id = ?;", (product_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found.")

        ings = None
        if row["ingredients_parsed"]:
            try:
                ings = json.loads(row["ingredients_parsed"])
            except Exception:
                ings = None
        if not ings and row["ingredients_raw"]:
            ings = row["ingredients_raw"]
        if not ings:
            ings = []

        dermo_res = evaluate_product_dermo(ings, skin_type=skin_type)
        return {
            "product_id": row["id"],
            "title": row["title"],
            "brand": row["brand"],
            "ingredients_present": bool(ings),
            "evaluation": dermo_res
        }
    finally:
        conn.close()

@router.get("/dermo/ingredients")
def list_clinical_ingredients_endpoint():
    """Lists all clinical dermatological ingredients stored in the database."""
    return load_clinical_ingredients_from_db()

@router.post("/dermo/ingredients")
def create_clinical_ingredient_endpoint(req: ClinicalIngredientCreateRequest):
    """
    Adds or updates a clinical ingredient in the database and hot-reloads the dermatological engine cache.
    """
    ok = add_clinical_ingredient_to_db(
        inci=req.inci,
        common_name=req.common_name,
        functional_type=req.functional_type,
        comedogenicity=req.comedogenicity,
        irritancy=req.irritancy,
        tags=req.tags,
        evidence=req.evidence,
        contraindications=req.contraindications,
        notes=req.notes
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save clinical ingredient to database.")
    reload_clinical_db()
    return {
        "status": "success",
        "message": f"Clinical ingredient '{req.inci}' persisted and clinical engine cache reloaded.",
        "ingredient": req.model_dump() if hasattr(req, "model_dump") else req.dict()
    }
