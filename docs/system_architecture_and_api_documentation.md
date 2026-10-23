# SkinSync System Architecture & API Reference Manual

> **Version:** 3.0 Production  
> **Environment:** Linux / SQLite 3 WAL Mode / FastAPI / Python 3.12+  
> **Last Updated:** October 2026  

---

## 1. Executive Summary & Change Log

This document provides a comprehensive technical reference for all enhancements, architectural reorganizations, and API operations implemented across the SkinSync platform.

### Summary of Major Architectural Changes
1. **Monolithic Decomposition of `app.py`:** Reduced `api/app.py` from 1,872 lines to 109 lines by decoupling the application into a modular architecture:
   - **`api/services/`**: In-memory catalog caching (`catalog_service.py`), formulation similarity and SOTA recommendation algorithms (`recommender.py`).
   - **`api/routers/`**: 8 functional domain routers implementing standard FastAPI `APIRouter` controllers.
2. **Database-Backed Clinical Rules & Synonyms:** Replaced hardcoded lists with relational database tables (`ingredient_synonyms` and `clinical_ingredients`) in `skincare.db` with dynamic REST endpoints and real-time cache reloading.
3. **Decoupled AI Workers with Batching:** Separated `jev_worker.py` from `gemini_worker.py`. Implemented structured Gemini batching (`run_gemini_verification_queue`) with configurable batch sizes to stay strictly under RPM (15) and RPD (1,500) limits.
4. **Pipeline Control Tables & Checkpoints:** Created thread-safe control tables (`pipeline_tasks`, `pipeline_subtasks`, `pipeline_task_logs`, `scrape_checkpoints`) providing atomic progress tracking, subtask decomposition, and granular diagnostics.
5. **Centralized Rotational Logging:** Built `pipeline/logging_config.py` with size/time-based rotating handlers, dual streams (`skincare_pipeline.log` for INFO+ and `skincare_error.log` for ERROR+), contextual task tracking, and REST monitoring endpoints.
6. **Title Enrichment & Truncation Remediation:** Implemented `pipeline/title_enricher.py` with 4-tier title canonicalization (v10 curated lookup, URL handle reconciliation, bare noun brand prefixing, and SPF-preserving punctuation cleanup). Enriched 1,919 catalog records in `skincare.db` and added hover tooltips in `skinsync-app/src/App.jsx`.
7. **Strict Import Sanitization:** Eliminated all top-level `"repo."` import references across the entire codebase.

---

## 2. System Architecture & Directory Structure

```
skincare/
├── repo/
│   ├── api/
│   │   ├── app.py                      # Main entrypoint: loads env, initializes logging, mounts routers (109 lines)
│   │   ├── skincare.db                 # SQLite database in WAL mode (7,424+ products, INCI knowledge, control tables)
│   │   ├── dermo_data.py               # Clinical dermatological database, comedogenicity/irritancy models
│   │   ├── simple_db.py                # JSON database adapter for user onboarding
│   │   ├── master_product_database_v10_final.csv # Fallback curated product reference
│   │   │
│   │   ├── services/                   # Core Domain Logic & Engine Services
│   │   │   ├── __init__.py
│   │   │   ├── catalog_service.py      # Catalog in-memory cache (df_cache), parsing, actives, load_catalog_from_db
│   │   │   └── recommender.py          # Weighted Jaccard, safety scoring, FastEmbed cross-encoder, Jev/Gemini
│   │   │
│   │   ├── routers/                    # Modular FastAPI Route Controllers
│   │   │   ├── __init__.py
│   │   │   ├── recommendations.py      # /recommend, /search, /onboarding, /user, /stats, /admin/sync, /synonyms
│   │   │   ├── catalog.py              # /products/refresh, /stores/{id}/refresh, /products/enrich-titles
│   │   │   ├── inci.py                 # /inci/match, /products/{id}/match-inci, /inci/batch-match
│   │   │   ├── ingredients.py          # /products/{id}/fill-ingredients, /products/ingredients/batch-fill
│   │   │   ├── similarity.py           # /similarity/score
│   │   │   ├── dermo.py                # /dermo/score, /products/{id}/dermo-score, /dermo/ingredients
│   │   │   ├── tasks.py                # /tasks, /tasks/{id}, /tasks/{id}/cancel, /tasks/{id}/subtasks, /checkpoints
│   │   │   └── logs.py                 # /logs/files, /logs/recent, /logs/rotate
│   │   │
│   │   ├── pipeline/                   # Data Ingestion, Scraping, AI Workers & Infrastructure
│   │   │   ├── config.py               # Centralized configuration & environment constants
│   │   │   ├── db_manager.py           # Thread-safe SQLite connection manager with WAL & busy_timeout
│   │   │   ├── logging_config.py       # Centralized rotational logging & contextual task tracking
│   │   │   ├── task_tracker.py         # Pipeline control tables, progress updates, and event logger
│   │   │   ├── title_enricher.py       # Product title canonicalization & handle reconciliation engine
│   │   │   ├── harvester.py            # Shopify store harvesting with rate-limiting & title enrichment
│   │   │   ├── inci_matcher.py         # 4-Gate INCI matcher with FastEmbed vector search & safeguards
│   │   │   ├── jev_worker.py           # Decoupled Jev 1.13 decision model worker
│   │   │   ├── gemini_worker.py        # Decoupled Gemini 3.5 Flash-Lite batch verification worker
│   │   │   ├── safeguards.py           # CosmeticCompatibilityValidator deterministic rules
│   │   │   └── daily_sync.py           # Orchestration runner for scheduled catalog updates
│   │   │
│   │   ├── logs/                       # Rotational log directory
│   │   │   ├── skincare_pipeline.log   # Active application log (rotating at 10MB, up to 5 backups)
│   │   │   └── skincare_error.log      # Active error log (rotating at 5MB, up to 3 backups)
│   │   │
│   │   └── tests/                      # Automated Test Suite (45 Unit & Integration Tests)
│   │       ├── test_api_operations.py
│   │       ├── test_control_tables.py
│   │       ├── test_evaluation_benchmark.py
│   │       ├── test_rotational_logging.py
│   │       ├── test_title_enrichment.py
│   │       └── test_workers.py
│   │
│   ├── skinsync-app/                   # React + Vite + Tailwind Frontend
│   │   └── src/App.jsx                 # Search, recommendation cards, and full-title tooltips
│   │
│   └── docs/                           # Engineering Documentation
│       ├── README.md
│       ├── technical_design_document.md
│       ├── enhancement_plan.md
│       ├── sota_architecture_evaluation.md
│       └── system_architecture_and_api_documentation.md
```

---

## 3. Database Architecture & Control Tables

SkinSync utilizes a SQLite 3 relational database (`skincare.db`) configured in **Write-Ahead Logging (WAL)** mode with a `busy_timeout` of 30,000 ms to support high concurrency between API read requests and background ingestion workers.

### Relational Schema Definitions

#### 1. `pipeline_tasks` (Master Pipeline Control Table)
Tracks top-level task lifecycle, progress counters, execution stages, and durations:
```sql
CREATE TABLE IF NOT EXISTS pipeline_tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    target_source TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    params TEXT,
    fields TEXT,
    total_items INTEGER DEFAULT 0,
    processed_items INTEGER DEFAULT 0,
    successful_items INTEGER DEFAULT 0,
    failed_items INTEGER DEFAULT 0,
    current_stage TEXT,
    progress_percent REAL DEFAULT 0.0,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### 2. `pipeline_subtasks` (Granular Work Unit Decompositions)
Tracks page-level scraping, product chunk matches, or ingredient extraction steps:
```sql
CREATE TABLE IF NOT EXISTS pipeline_subtasks (
    subtask_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    total_steps INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    items_count INTEGER DEFAULT 0,
    items_succeeded INTEGER DEFAULT 0,
    items_failed INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE
);
```

#### 3. `pipeline_task_logs` (Chronological Structured Event Logs)
Persists time-stamped log entries tied to specific tasks and subtasks:
```sql
CREATE TABLE IF NOT EXISTS pipeline_task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    subtask_id TEXT,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    context_data TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE
);
```

#### 4. `scrape_checkpoints` (Store Ingestion State Cursors)
Preserves store scraping cursors to resume interrupted crawls without duplicate work:
```sql
CREATE TABLE IF NOT EXISTS scrape_checkpoints (
    store_id TEXT PRIMARY KEY,
    last_page INTEGER DEFAULT 0,
    total_products_scraped INTEGER DEFAULT 0,
    last_product_id TEXT,
    last_scraped_at TEXT,
    status TEXT DEFAULT 'idle',
    error_message TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### 5. `ingredient_synonyms` (Database-Backed INCI Synonyms)
Replaces hardcoded dictionaries with dynamic, editable aliases:
```sql
CREATE TABLE IF NOT EXISTS ingredient_synonyms (
    alias TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### 6. `clinical_ingredients` (Database-Backed Dermatological Safety)
Stores comedogenicity ratings (0–5), irritancy ratings (0–5), and contraindications:
```sql
CREATE TABLE IF NOT EXISTS clinical_ingredients (
    inci TEXT PRIMARY KEY,
    common_name TEXT,
    functional_type TEXT NOT NULL DEFAULT 'active',
    comedogenicity INTEGER DEFAULT 0,
    irritancy INTEGER DEFAULT 0,
    tags TEXT,
    evidence TEXT,
    contraindications TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. API Reference & Endpoint Usage Guide

The API runs on FastAPI and exposes 33 specialized endpoints categorized across 8 routers.

### 4.1. Recommendations & User Search Router (`/`)
Handles customer-facing interactions, product search, stats, and synonym management.

#### `POST /recommend`
Finds cosmetic duplicates and alternatives for a target product based on hybrid formulation matching, active overlap, and skin type safety scoring.

**Request Payload:**
```json
{
  "product_title": "The Ordinary Niacinamide 10% + Zinc 1%",
  "user_skin_type": "oily"
}
```

**Response Payload (`200 OK`):**
```json
{
  "user_skin": "oily",
  "source_product": {
    "title": "The Ordinary Niacinamide 10% + Zinc 1%",
    "brand": "The Ordinary",
    "price": 450.0,
    "match_score": 100.0,
    "image_url": "https://example.com/cdn/to-niacinamide.jpg",
    "warnings": null,
    "url": "https://example.com/products/to-niacinamide"
  },
  "results": [
    {
      "id": 142,
      "title": "Dermactive Acti-White Correcting Serum - Vitamin C",
      "brand": "Dermactive",
      "price": 280.0,
      "match_score": 92.4,
      "cross_score": 0.8842,
      "ai_verified": true,
      "ai_confidence": 95.0,
      "ai_provider": "gemini-3.5-flash-lite",
      "image_url": "https://example.com/cdn/dermactive.jpg",
      "warnings": null,
      "url": "https://example.com/products/dermactive-acti-white"
    }
  ]
}
```

#### `GET /search?query={q}`
Typeahead autocomplete search returning up to 8 matching product titles.

#### `GET /stats`
Returns live catalog metrics, database counts, and recent synchronization history.

#### `GET /synonyms`
Returns a JSON dictionary of all active ingredient aliases mapped to canonical names.

#### `POST /synonyms`
Adds or updates an ingredient synonym in the database and hot-reloads runtime caches.

---

### 4.2. Catalog & Ingestion Router (`/products`, `/stores`)
Controls selective attribute refreshes, store syncs, and title enrichment.

#### `POST /products/refresh`
Refreshes catalog products across a specific source or all sources, selectively updating specific fields (`price`, `stock`, `image`, `ingredients`, or `all`).

**Request Payload:**
```json
{
  "store_id": "bashrety",
  "fields": ["price", "stock"],
  "max_pages": 5,
  "background": true
}
```

#### `POST /stores/{store_id}/refresh`
Refreshes all products belonging to a specific store domain.

#### `POST /products/enrich-titles`
Runs the title enrichment engine across all products in the database.

---

### 4.3. INCI Bioequivalence Matching Router (`/inci`)
Provides formulation matching with granular scoring mode control and batch execution.

#### `POST /inci/match`
On-demand INCI matching for arbitrary brand and title strings (`scoring_mode`: `"local"`, `"jev"`, `"gemini"`, `"all"`).

#### `POST /products/{product_id}/match-inci`
Matches an existing catalog product against the INCI knowledge base and persists Gate 1–4 telemetry to the database.

#### `POST /inci/batch-match`
Batch matches catalog products with pipeline task tracking.

---

### 4.4. Ingredients Extraction & Auto-Fill Router (`/products`)
Extracts missing ingredient formulations from Shopify HTML product descriptions or matched INCI reference records.

#### `POST /products/{product_id}/fill-ingredients`
Fills formulation ingredients for a single product (`source`: `"store"`, `"inci"`, or `"auto"`).

#### `POST /products/ingredients/batch-fill`
Batch processes products lacking ingredient lists.

---

### 4.5. Formulation Similarity Scoring Router (`/similarity`)
Calculates pairwise bioequivalence and cosmetic compatibility between two products.

#### `POST /similarity/score`
Calculates formulation similarity between either database product IDs or custom product payloads.

**Request Payload:**
```json
{
  "product_id_a": 1,
  "product_id_b": 142,
  "skin_type": "sensitive",
  "include_cross_encoder": true
}
```

---

### 4.6. Clinical Dermatology Router (`/dermo`)
Evaluates comedogenicity and irritancy ratings and manages clinical ingredient definitions.

#### `POST /dermo/score`
Evaluates comedogenic and irritant risk loads for an ingredient list or text string.

#### `GET /products/{product_id}/dermo-score?skin_type=acne_prone`
Retrieves a product from the database catalog and calculates its dermatological risk profile.

#### `GET /dermo/ingredients`
Lists all clinical ingredients in the database.

#### `POST /dermo/ingredients`
Adds or updates a clinical ingredient with its evidence, comedogenicity, and irritancy.

---

### 4.7. Pipeline Tasks & Checkpoint Router (`/tasks`, `/scraping`)
Provides real-time visibility into long-running tasks, progress percentages, and subtasks.

#### `GET /tasks?status=running&task_type=refresh_catalog&limit=20`
Lists active or completed pipeline tasks with filtering and pagination.

#### `GET /tasks/{task_id}`
Returns complete task telemetry, item counters, current stage, and duration.

#### `POST /tasks/{task_id}/cancel`
Cancels a running or pending task.

#### `GET /tasks/{task_id}/subtasks`
Retrieves granular subtasks (e.g. per-page crawling, chunk batches).

#### `GET /tasks/{task_id}/logs`
Returns chronological log events for a task.

#### `GET /scraping/checkpoints`
Lists store crawl cursors showing last scraped page and status.

---

### 4.8. Centralized Rotational Logging Router (`/logs`)
Provides operational log inspection, rotation controls, and diagnostics.

#### `GET /logs/files`
Inventories all active and rotated log files on disk.

#### `GET /logs/recent?limit=50&level=ERROR`
Streams the most recent lines from the primary log file with optional level filtering (`INFO`, `WARNING`, `ERROR`).

#### `POST /logs/rotate`
Forces immediate rotation of active file handlers.

---

## 5. Data Quality: Title Enrichment & Truncation Remediation

### 4-Tier Remediation Strategy
Implemented in [`pipeline/title_enricher.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/title_enricher.py):
1. **Curated v10 Mapping:** Exact match lookup against `master_product_database_v10_final.csv` full titles.
2. **Handle Qualifier Reconciliation (`reconcile_title_from_handle`):** Extracts missing cosmetic qualifiers, preserves compound attributes (`Aluminum-Free`, `Fragrance-Free`, `Oil-Free`, `Vitamin C`), and filters campaign noise (`-eid`, `-30ml`).
3. **Brand Prefixing on Generic Nouns:** Prefixes brand to titles containing bare cosmetic nouns with normalized alphanumeric deduplication.
4. **Punctuation Sanitization:** Removes trailing hyphens, commas, and ellipses while strictly preserving cosmetic ratings ending in `+` (`SPF50+`, `PA++++`).

### Production Migration Results
- **Total Catalog Products Processed:** 7,424
- **Titles Enriched:** **1,919 (25.8% of catalog)**
  - Curated v10 Mappings: 1,195
  - Brand Prefixes Added: 610
  - Handle Qualifiers Reconciled: 114
  - Other / Cleanups: 0

---

## 6. Decoupled AI Verification Workers & Batching

### 1. Jev Worker (`pipeline/jev_worker.py`)
- Evaluates products queued with `gate4_jev_status = 'pending_jev'`.
- On HTTP 429 rate limit, hands off products to `pending_gemini` without data loss.

### 2. Gemini Batch Worker (`pipeline/gemini_worker.py`)
- Evaluates products queued with `gate4_gemini_status = 'pending_gemini'`.
- Consolidates products into batches (default batch size: 10) within a single structured prompt.
- Reduces API calls by 90%, easily sustaining operations under 15 RPM and 1,500 RPD quotas.

---

## 7. Automated Test Suite & Verification

The test suite contains **45 comprehensive unit and integration tests** verifying all layers of the platform:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

| Test Module | Coverage | Status |
| :--- | :--- | :--- |
| `tests/test_api_operations.py` | Catalog refresh, INCI match, ingredients fill, similarity score, dermo score | **Passed (15/15)** |
| `tests/test_title_enrichment.py` | Brand detection, SPF preservation, handle reconciliation, API endpoints | **Passed (10/10)** |
| `tests/test_control_tables.py` | Task creation, subtasks, event logging, checkpoints, cancellation | **Passed (8/8)** |
| `tests/test_rotational_logging.py` | File rotation, size triggers, level filtering, log context injection | **Passed (6/6)** |
| `tests/test_workers.py` | Jev worker, Gemini batch processing, queue handoffs | **Passed (4/4)** |
| `tests/test_evaluation_benchmark.py` | SOTA benchmark accuracy, cross-encoder latency | **Passed (2/2)** |
| **Total** | **45 Comprehensive Tests (Execution Time: ~14.2s)** | **100% OK** |
