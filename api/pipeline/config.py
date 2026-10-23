"""
Centralized Configuration for SkinSync Ingestion, Matching, and AI Pipeline.
All parameters can be tuned via environment variables (or .env file) without code changes.
"""

import os
from dataclasses import dataclass

def _load_env_file():
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

_load_env_file()

def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return default

def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is not None:
        try:
            return float(val)
        except ValueError:
            pass
    return default

def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is not None:
        return val.strip().lower() in ("1", "true", "yes", "on")
    return default

@dataclass(frozen=True)
class PipelineConfig:
    # -------------------------------------------------------------------------
    # 1. Gate 1: INCI Quality & Entity Validation
    # -------------------------------------------------------------------------
    # Minimum distinct chemical entities required (set to 3 as requested)
    MIN_INGREDIENT_COUNT: int = _env_int("MIN_INGREDIENT_COUNT", 3)
    MIN_INGREDIENT_RAW_LEN: int = _env_int("MIN_INGREDIENT_RAW_LEN", 12)
    MAX_INGREDIENT_RAW_LEN: int = _env_int("MAX_INGREDIENT_RAW_LEN", 2500)

    # -------------------------------------------------------------------------
    # 2. Vector Search & Hybrid Candidate Scoring
    # -------------------------------------------------------------------------
    FASTEMBED_MODEL_NAME: str = _env_str("FASTEMBED_MODEL_NAME", "BAAI/bge-small-en-v1.5")
    DENSE_VECTOR_WEIGHT: float = _env_float("DENSE_VECTOR_WEIGHT", 0.55)
    SPARSE_TOKEN_WEIGHT: float = _env_float("SPARSE_TOKEN_WEIGHT", 0.45)
    TOP_K_CANDIDATES: int = _env_int("TOP_K_CANDIDATES", 5)

    # -------------------------------------------------------------------------
    # 3. Local ONNX Cross-Encoder Reranker
    # -------------------------------------------------------------------------
    ENABLE_CROSS_ENCODER: bool = _env_bool("ENABLE_CROSS_ENCODER", True)
    CROSS_ENCODER_MODEL_NAME: str = _env_str("CROSS_ENCODER_MODEL_NAME", "Xenova/ms-marco-MiniLM-L-6-v2")
    CROSS_ENCODER_CONFIDENCE_THRESHOLD: float = _env_float("CROSS_ENCODER_CONFIDENCE_THRESHOLD", 0.85)

    # -------------------------------------------------------------------------
    # 4. Decision Thresholds (Auto-Approve vs. Ambiguity vs. Reject)
    # -------------------------------------------------------------------------
    # Score at or above which candidate is auto-approved without LLM calls
    AUTO_APPROVE_HYBRID_THRESHOLD: float = _env_float("AUTO_APPROVE_HYBRID_THRESHOLD", 0.93)
    # Score below which candidate is discarded without calling Jev / Gemini
    AMBIGUITY_ZONE_LOW_THRESHOLD: float = _env_float("AMBIGUITY_ZONE_LOW_THRESHOLD", 0.45)
    # Conservative threshold if Jev API is unreachable
    CONSERVATIVE_FALLBACK_THRESHOLD: float = _env_float("CONSERVATIVE_FALLBACK_THRESHOLD", 0.88)

    # -------------------------------------------------------------------------
    # 5. Gate 4: Jev 1.13 AI Verification (Primary)
    # -------------------------------------------------------------------------
    JEV_MODEL_NAME: str = _env_str("JEV_MODEL_NAME", "jev-1.13-free")
    JEV_CONFIDENCE_THRESHOLD: float = _env_float("JEV_CONFIDENCE_THRESHOLD", 0.70)
    JEV_REQUEST_INTERVAL_SECONDS: float = _env_float("JEV_REQUEST_INTERVAL_SECONDS", 2.0)
    JEV_RATE_LIMIT_BACKOFF_SECONDS: float = _env_float("JEV_RATE_LIMIT_BACKOFF_SECONDS", 10.0)
    JEV_TIMEOUT_SECONDS: float = _env_float("JEV_TIMEOUT_SECONDS", 8.0)

    # -------------------------------------------------------------------------
    # 6. Gate 4: Gemini 3.5 Flash-Lite Fallback (When Jev is Rate Limited or Unavailable)
    # -------------------------------------------------------------------------
    GEMINI_API_KEY: str = _env_str("GEMINI_API_KEY", "")
    GEMINI_MODEL_NAME: str = _env_str("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
    GEMINI_TIMEOUT_SECONDS: float = _env_float("GEMINI_TIMEOUT_SECONDS", 35.0)
    GEMINI_CONFIDENCE_THRESHOLD: float = _env_float("GEMINI_CONFIDENCE_THRESHOLD", 0.70)
    GEMINI_MAX_RPM: int = _env_int("GEMINI_MAX_RPM", 15)
    GEMINI_MAX_RPD: int = _env_int("GEMINI_MAX_RPD", 1500)
    GEMINI_PACE_DELAY_SECONDS: float = _env_float("GEMINI_PACE_DELAY_SECONDS", 4.0)
    GEMINI_BATCH_SIZE: int = _env_int("GEMINI_BATCH_SIZE", 10)
    GEMINI_QUEUE_BATCH_SIZE: int = _env_int("GEMINI_QUEUE_BATCH_SIZE", 50)

    # -------------------------------------------------------------------------
    # 7. Crawler & Scraping Engine
    # -------------------------------------------------------------------------
    SHOPIFY_PAGE_LIMIT: int = _env_int("SHOPIFY_PAGE_LIMIT", 250)
    SHOPIFY_DEFAULT_DELAY_SECONDS: float = _env_float("SHOPIFY_DEFAULT_DELAY_SECONDS", 0.35)
    SHOPIFY_MAX_RETRIES: int = _env_int("SHOPIFY_MAX_RETRIES", 3)
    SHOPIFY_TIMEOUT_SECONDS: float = _env_float("SHOPIFY_TIMEOUT_SECONDS", 12.0)
    DEFAULT_MAX_PAGES_PER_STORE: int = _env_int("DEFAULT_MAX_PAGES_PER_STORE", 40)
    PRICE_DIFFERENCE_THRESHOLD: float = _env_float("PRICE_DIFFERENCE_THRESHOLD", 0.01)
    MIN_STORE_ENRICH_LEN: int = _env_int("MIN_STORE_ENRICH_LEN", 20)

    # -------------------------------------------------------------------------
    # 8. Strategy B: Open Beauty Facts Live Fallback
    # -------------------------------------------------------------------------
    OPENBEAUTYFACTS_TIMEOUT_SECONDS: float = _env_float("OPENBEAUTYFACTS_TIMEOUT_SECONDS", 5.0)
    OPENBEAUTYFACTS_PAGE_SIZE: int = _env_int("OPENBEAUTYFACTS_PAGE_SIZE", 5)

    # -------------------------------------------------------------------------
    # 9. Worker Batch Processing
    # -------------------------------------------------------------------------
    INCI_BACKFILL_BATCH_SIZE: int = _env_int("INCI_BACKFILL_BATCH_SIZE", 50)
    JEV_QUEUE_BATCH_SIZE: int = _env_int("JEV_QUEUE_BATCH_SIZE", 25)

    # -------------------------------------------------------------------------
    # 10. Centralized Rotational Logging Configuration
    # -------------------------------------------------------------------------
    LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO")
    LOG_DIR: str = _env_str("LOG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
    LOG_FILE_NAME: str = _env_str("LOG_FILE_NAME", "skincare.log")
    LOG_ERROR_FILE_NAME: str = _env_str("LOG_ERROR_FILE_NAME", "error.log")
    LOG_MAX_BYTES: int = _env_int("LOG_MAX_BYTES", 10 * 1024 * 1024)  # 10 MB per log file
    LOG_BACKUP_COUNT: int = _env_int("LOG_BACKUP_COUNT", 5)           # Keep 5 rotated archives
    LOG_ROTATION_TYPE: str = _env_str("LOG_ROTATION_TYPE", "size")     # 'size' or 'time'
    LOG_TIME_INTERVAL: str = _env_str("LOG_TIME_INTERVAL", "midnight") # for time-based rotation
    LOG_TIME_BACKUP_DAYS: int = _env_int("LOG_TIME_BACKUP_DAYS", 14)  # 14 days retention
    LOG_TO_CONSOLE: bool = _env_bool("LOG_TO_CONSOLE", True)
    LOG_TO_FILE: bool = _env_bool("LOG_TO_FILE", True)
    LOG_JSON_FORMAT: bool = _env_bool("LOG_JSON_FORMAT", False)

# Global singleton configuration instance
cfg = PipelineConfig()
