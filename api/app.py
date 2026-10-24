"""
Egyptian Skincare AI Production Engine: FastAPI Application Entrypoint.
Modularized architecture using APIRouter for clean domain separation.
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pipeline.logging_config import setup_logging, get_logger
from services.catalog_service import (
    load_catalog_from_db, get_catalog_df, get_synonyms, set_synonyms,
    KEY_ACTIVES, SKIN_BENEFITS, SKIN_RISKS, DB_PATH
)
from services.recommender import get_recommendations

from routers.recommendations import router as recommendations_router
from routers.catalog import router as catalog_router
from routers.inci import router as inci_router
from routers.ingredients import router as ingredients_router
from routers.similarity import router as similarity_router
from routers.dermo import router as dermo_router
from routers.tasks import router as tasks_router
from routers.logs import router as logs_router
from routers.prices import router as prices_router
from routers.admin import router as admin_router
from routers.aggregation import router as aggregation_router
from routers.workflows import router as workflows_router
from routers.catalog_reset import router as catalog_reset_router
from routers.inci_research import router as inci_research_router

setup_logging()
logger = get_logger("skincare_engine")

# ==============================================================================
# 1. LOAD .ENV CONFIGURATION
# ==============================================================================
def load_env_file():
    """
    Loads environment variables from .env files.
    Supports python-dotenv if installed, with a zero-dependency fallback.
    """
    try:
        from dotenv import load_dotenv
        env_file = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file)
        load_dotenv()
    except ImportError:
        pass

    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
            except Exception as e:
                logger.warning(f"Failed to read .env file at {c}: {e}")

    if os.environ.get("OPENCODE_API_KEY"):
        key = os.environ.get("OPENCODE_API_KEY")
        logger.info("Loaded OPENCODE_API_KEY from environment.")
    elif os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY"):
        logger.info("Loaded JEV_API_KEY / TYPESAFE_API_KEY from environment.")

load_env_file()
setup_logging(force_reconfigure=True)

# ==============================================================================
# 2. FASTAPI APPLICATION SETUP & ROUTER MOUNTING
# ==============================================================================
app = FastAPI(
    title="Egyptian Skincare AI Production Engine",
    description="Real-time multi-store scraper, INCI bioequivalence matcher, and Jev 1.13 reranker",
    version="3.0"
)

from security.admin import AdminAuthentication
app.add_middleware(AdminAuthentication)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("SKINSYNC_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip() and origin.strip() != "*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

from fastapi import Request
@app.get("/admin/access")
def admin_access(request: Request):
    return request.state.admin

# Register modular domain routers
app.include_router(recommendations_router)
app.include_router(catalog_router)
app.include_router(inci_router)
app.include_router(ingredients_router)
app.include_router(similarity_router)
app.include_router(dermo_router)
app.include_router(tasks_router)
app.include_router(logs_router)
app.include_router(prices_router)
app.include_router(admin_router)
app.include_router(aggregation_router)
app.include_router(workflows_router)
app.include_router(catalog_reset_router)
app.include_router(inci_research_router)

# ==============================================================================
# 3. BACKWARDS COMPATIBILITY EXPORTS
# ==============================================================================
def __getattr__(name: str):
    """Dynamically resolves legacy globals from services for backwards compatibility."""
    if name == "df_cache":
        return get_catalog_df()
    if name == "SYNONYMS":
        return get_synonyms()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")