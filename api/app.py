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
        logger.info(f"Loaded OPENCODE_API_KEY from environment ({key[:4]}...{key[-3:]})")
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register modular domain routers
app.include_router(recommendations_router)
app.include_router(catalog_router)
app.include_router(inci_router)
app.include_router(ingredients_router)
app.include_router(similarity_router)
app.include_router(dermo_router)
app.include_router(tasks_router)
app.include_router(logs_router)

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