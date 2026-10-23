"""
Modular API Router: Multi-Source Price Comparison and Product Clustering Endpoints.
"""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import Dict, List, Any, Optional

from services.price_service import (
    get_product_price_comparison,
    get_canonical_price_comparison,
    get_price_arbitrage_deals
)
from pipeline.product_linker import link_catalog_products
from pipeline.logging_config import get_logger

router = APIRouter(tags=["Price Comparison & Cross-Source Linking"])
logger = get_logger("skincare_engine.routers.prices")


@router.get("/products/{product_id}/price-comparison")
def compare_product_prices(product_id: int):
    """
    Compares the price of a product listing across all available retail sources in Egypt.
    Returns best deal, in-stock offers, price range, and potential consumer savings.
    """
    res = get_product_price_comparison(product_id)
    if not res:
        raise HTTPException(
            status_code=404,
            detail=f"Product listing {product_id} has not yet been linked to a canonical product cluster."
        )
    return res


@router.get("/canonical-products/{canonical_id}/offers")
def get_canonical_offers(canonical_id: int):
    """
    Returns all retail store offers and pricing for a canonical product cluster.
    """
    res = get_canonical_price_comparison(canonical_id)
    if not res:
        raise HTTPException(
            status_code=404,
            detail=f"Canonical product entity {canonical_id} not found."
        )
    return res


@router.get("/products/price-deals")
def get_price_deals(
    limit: int = Query(20, ge=1, le=100, description="Max number of arbitrage deals to return"),
    min_savings: float = Query(20.0, ge=0.0, description="Minimum price divergence between stores in EGP")
):
    """
    Finds the highest-value price divergence and consumer arbitrage deals across Egyptian pharmacies.
    """
    deals = get_price_arbitrage_deals(limit=limit, min_savings_egp=min_savings)
    return {
        "count": len(deals),
        "min_savings_filter": min_savings,
        "deals": deals
    }


@router.post("/products/link-clusters")
def trigger_product_clustering(background_tasks: BackgroundTasks = None):
    """
    Triggers cross-source product entity resolution, canonical clustering, and ingredient propagation.
    """
    try:
        res = link_catalog_products()
        return {
            "status": "success",
            "message": "Cross-source product clustering and ingredient propagation completed successfully.",
            "metrics": res
        }
    except Exception as e:
        logger.error(f"Product clustering failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
