# SkinSync System Architecture & API Reference Manual

> **Version:** 3.0 Production  
> **Environment:** Linux / SQLite 3 WAL Mode / FastAPI / Python 3.12+  
> **Last Updated:** October 2026  

---

## 1. Executive Summary & Change Log

This document provides a comprehensive technical reference for all enhancements, architectural reorganizations, and API operations implemented across the SkinSync platform.

### Summary of Major Architectural Changes
1. **Monolithic Decomposition of `app.py`:** Reduced `api/app.py` from 1,872 lines to 121 lines by decoupling the application into a modular architecture:
   - **`api/services/`**: In-memory catalog caching (`catalog_service.py`), formulation similarity and SOTA recommendation algorithms (`recommender.py`), multi-store price comparisons and arbitrage deal tracking (`price_service.py`).
   - **`api/routers/`**: 10 functional domain routers implementing standard FastAPI `APIRouter` controllers.
2. **Database-Backed Clinical Rules & Synonyms:** Replaced hardcoded lists with relational database tables (`ingredient_synonyms` and `clinical_ingredients`) in `skincare.db` with dynamic REST endpoints and real-time cache reloading.
3. **Decoupled AI Workers with Batching:** Separated `jev_worker.py` from `gemini_worker.py`. Implemented structured Gemini batching (`run_gemini_verification_queue`) with configurable batch sizes to stay strictly under RPM (15) and RPD (1,500) limits.
4. **Pipeline Control Tables & Checkpoints:** Created thread-safe control tables (`pipeline_tasks`, `pipeline_subtasks`, `pipeline_task_logs`, `scrape_checkpoints`) providing atomic progress tracking, subtask decomposition, and granular diagnostics.
5. **Centralized Rotational Logging:** Built `pipeline/logging_config.py` with size/time-based rotating handlers, dual streams (`skincare_pipeline.log` for INFO+ and `skincare_error.log` for ERROR+), contextual task tracking, and REST monitoring endpoints.
6. **Title Enrichment & Truncation Remediation:** Implemented `pipeline/title_enricher.py` with 4-tier title canonicalization (v10 curated lookup, URL handle reconciliation, bare noun brand prefixing, and SPF-preserving punctuation cleanup). Enriched 1,919 catalog records in `skincare.db` and added hover tooltips in `skinsync-app/src/App.jsx`.
7. **Strict Import Sanitization:** Eliminated all top-level `"repo."` import references across the entire codebase.
8. **INCI Matcher Bottleneck Resolutions:**
   - **Case-Insensitive Token Matching:** Resolved RapidFuzz byte-comparison penalty on uppercase product titles (`KERACNYL` vs `Keracnyl`), elevating token match from 20.8% to 98.2% and auto-approving directly with 0 LLM calls.
   - **Gate 1 Fallback Guard:** Added `top_candidate_lacked_ingredients` guard to prevent false positive matches against distant low-scoring siblings ($>0.15$ hybrid gap) when the top title candidate lacks ingredients in the knowledge base.
   - **Store-to-Store INCI Ingestion:** Ingested 1,727 high-quality store-verified ingredient formulations from Egyptian pharmacy product descriptions into `inci_reference` under `source = 'store_verified'`.
9. **Cross-Source Product Entity Resolution & Price Comparison:**
   - **Entity Resolution Engine (`pipeline/product_linker.py`):** Clustered 22,768 store listings into 21,680 canonical products with packaging volume extraction (`extract_volume_and_normalize`) to prevent distinct container sizes from collapsing.
   - **Cross-Source Ingredient Propagation:** Propagated verified formulations across stores, expanding products with full ingredients to **3,492 listings** (+273 listings).
   - **Real-Time Price Comparison & Arbitrage Engine (`services/price_service.py`, `routers/prices.py`):** Uncovered 930 multi-store clusters with price arbitrage spreads up to 62.5% savings across Egyptian retailers.
10. **Admin Observatory & Deep Forensic Telemetry Portal:**
   - **Observatory Router (`routers/admin.py`):** Exposes `/admin/overview`, `/admin/products`, and `/admin/products/{id}/telemetry` for complete visibility into scraped HTML descriptions, ingredient token trees, 4-gate INCI decision scores, multi-store pricing tables, and clinical safety ratings.
   - **Interactive Admin UI (`skinsync-app/src/AdminObservatory.jsx`):** Seamless toggle between consumer matching and forensic catalog explorer with 6-tab inspection drawers and multi-filter criteria.
11. **Taxonomy Classification & Phase 1 Non-Skincare Suppression:**
   - **Deterministic Taxonomy Engine (`pipeline/classifier.py`):** Establishes 12 active skincare & body categories (`Cleanser`, `Moisturizer`, `Serum`, `Sunscreen`, `Tanning`, `Body Care`, `Toner`, `Exfoliator`, `Eye Care`, `Mask`, `Lip Care`, `Treatment`) and 12 suppressed non-skincare categories (`Deodorant`, `Fragrance`, `Hair Care`, `Makeup`, `Nail Care`, `Supplements & Health`, `Medical Supplies`, `Oral Care`, `Tools & Accessories`, `Baby Care`, `Set`, `Other Non-Skincare`).
   - **Phase 1 Consumer Suppression:** All non-skincare listings (medical supplies, vitamins/dietary supplements, color cosmetics, nails, deodorants, perfumes, and haircare) are preserved in `skincare.db` (`is_skincare = 0`) for Phase 2 beauty expansion, but strictly suppressed from in-memory recommendation cache (`df_cache`), autocomplete search, price arbitrage, and INCI formulation matching. Catalog stands at **8,763 active skincare & body listings** and **14,037 suppressed items**.
12. **Automated Brand Enrichment in Product Titles:**
   - **Title Enricher Overhaul (`pipeline/title_enricher.py`):** Removed word-count restrictions and added Unicode NFD accent normalization (`strip_accents`) to ensure that all store listings lacking brand names (notably 72.1% of Source Beauty and 56.8% of Bashrety) are systematically prefixed with their canonical brand name (e.g. `"DermaActive SWEAT CONTROL ACTI-WHITE"`). Enriched **5,575 listings**.
13. **Hard Cross-Encoder Rejection Gate & Safeguard Rules:**
   - **Cosmetic Compatibility Validator (`pipeline/safeguards.py`):** Enforces strict brand clash checks (`brand_a` vs `brand_b`) and category incompatibility rules blocking deodorants, perfumes, and hair care from matching facial lotions, creams, serums, or sunscreens.
   - **Deterministic Rejection Gate (`pipeline/inci_matcher.py`):** Candidates with `cross_score < 0.35` or `token_sim < 0.25` are immediately flagged with `gate4_cross_status = 'cross_rejected'` and `gate4_gemini_status = 'rejected'`, eliminating false positives like `"SWEAT CONTROL ACTI-WHITE"` matching `"Derma: B Daily Moisture Body Lotion"` and consuming zero Gemini LLM quota.
14. **Token-Sorted Canonical Clustering:**
   - **Word-Order and Spelling Invariance (`pipeline/product_linker.py`):** Decomposes titles into normalized, sorted core tokens (`extract_volume_and_normalize`), neutralizing differences between UK/US spellings (`moisturising` vs `moisturizing`), filler adjectives (`daily`, `classic`), and word order permutations (`pm facial moisturizing lotion` vs `facial lotion pm`). Successfully linked previously separated CeraVe Moisturizing Lotions across Bloom Pharmacy, Feel22, and Source Beauty into unified cluster ID `22961`.

---

## 2. System Architecture & Directory Structure

```
skincare/
├── repo/
│   ├── api/
│   │   ├── app.py                      # Main entrypoint: loads env, initializes logging, mounts 10 routers (121 lines)
│   │   ├── skincare.db                 # SQLite database in WAL mode (22,768 listings, 21,680 canonical products)
│   │   ├── dermo_data.py               # Clinical dermatological database, comedogenicity/irritancy models
│   │   ├── simple_db.py                # JSON database adapter for user onboarding
│   │   ├── master_product_database_v10_final.csv # Fallback curated product reference
│   │   │
│   │   ├── services/                   # Core Domain Logic & Engine Services
│   │   │   ├── __init__.py
│   │   │   ├── catalog_service.py      # Catalog in-memory cache (df_cache), parsing, actives, load_catalog_from_db
│   │   │   ├── recommender.py          # Weighted Jaccard, safety scoring, FastEmbed cross-encoder, Jev/Gemini
│   │   │   └── price_service.py        # Multi-store price comparison, best deal detection & arbitrage tracking
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
│   │   │   ├── logs.py                 # /logs/files, /logs/recent, /logs/rotate
│   │   │   ├── prices.py               # /products/{id}/price-comparison, /canonical-products/{id}/offers, /products/price-deals, /products/link-clusters
│   │   │   └── admin.py                # /admin/overview, /admin/products, /admin/products/{id}/telemetry
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
│   │   │   ├── product_linker.py       # Cross-source product entity resolution & ingredient propagator
│   │   │   ├── safeguards.py           # CosmeticCompatibilityValidator deterministic rules
│   │   │   ├── run_pipeline_job.py     # 5-Phase pipeline orchestrator
│   │   │   └── daily_sync.py           # Orchestration runner for scheduled catalog updates
│   │   │
│   │   ├── logs/                       # Rotational log directory
│   │   │   ├── skincare_pipeline.log   # Active application log (rotating at 10MB, up to 5 backups)
│   │   │   └── skincare_error.log      # Active error log (rotating at 5MB, up to 3 backups)
│   │   │
│   │   └── tests/                      # Automated Test Suite (65 Unit & Integration Tests, 100% OK)
│   │       ├── test_api_operations.py
│   │       ├── test_control_tables.py
│   │       ├── test_evaluation_benchmark.py
│   │       ├── test_rotational_logging.py
│   │       ├── test_title_enrichment.py
│   │       ├── test_workers.py
│   │       ├── test_price_comparison.py
│   │       └── test_admin_router.py
│   │
│   ├── skinsync-app/                   # React + Vite + Tailwind Frontend
│   │   ├── src/App.jsx                 # Search, recommendation cards, full-title tooltips, and deals modal
│   │   └── src/AdminObservatory.jsx    # Full Admin Observatory & Telemetry Forensics Portal
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

#### 7. `canonical_products` (Cross-Source Product Clusters & Price Intelligence)
Aggregates identical products sold across multiple retail sources:
```sql
CREATE TABLE IF NOT EXISTS canonical_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_title TEXT NOT NULL,
    brand TEXT NOT NULL,
    category TEXT NOT NULL,
    normalized_key TEXT NOT NULL UNIQUE,
    ingredients_raw TEXT,
    ingredients_parsed TEXT,
    actives TEXT,
    ingredient_source TEXT,
    inci_matched_id INTEGER,
    min_price REAL DEFAULT 0.0,
    max_price REAL DEFAULT 0.0,
    store_count INTEGER DEFAULT 1,
    primary_image_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
Linked via `products.canonical_product_id` (foreign key indexed for sub-millisecond lookups).

---

## 4. API Reference & Endpoint Usage Guide

The API runs on FastAPI and exposes 37 specialized endpoints categorized across 9 routers.

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

### 4.9. Price Comparison & Cross-Source Linking Router (`/products`, `/canonical-products`)
Provides multi-source price intelligence, consumer savings calculations, and entity linking triggers.

#### `GET /products/{product_id}/price-comparison`
Compares the price of a store listing across all available retail sources in Egypt. Returns best deal, in-stock offers, price range, and potential consumer savings.
- **Example Response:**
  ```json
  {
    "canonical_product_id": 76,
    "title": "Isis Pharma Secalia Face Cream - 40ml",
    "brand": "Isis Pharma",
    "category": "Face Moisturizer",
    "min_price": 594.15,
    "max_price": 699.0,
    "price_difference_egp": 104.85,
    "savings_percent": 15.0,
    "store_count": 2,
    "listing_count": 2,
    "queried_product_id": 124,
    "best_deal": {
      "product_id": 124,
      "store_id": "bashrety",
      "store_name": "Bashrety",
      "price": 594.15,
      "is_available": 1,
      "is_best_deal": true
    },
    "offers": [
      {
        "product_id": 124,
        "store_id": "bashrety",
        "store_name": "Bashrety",
        "price": 594.15,
        "is_available": 1,
        "is_best_deal": true
      },
      {
        "product_id": 9812,
        "store_id": "bloompharmacy",
        "store_name": "Bloom Pharmacy",
        "price": 699.0,
        "is_available": 1,
        "is_best_deal": false
      }
    ]
  }
  ```

#### `GET /canonical-products/{canonical_id}/offers`
Returns all retail store offers, pricing breakdown, and product metadata for a canonical product cluster.

#### `GET /products/price-deals?limit=20&min_savings=20.0`
Returns top consumer price arbitrage deals where identical products are sold across multiple stores with significant price divergence.
- **Query Parameters:** `limit` (int, default 20), `min_savings` (float, default 20.0 EGP).

#### `POST /products/link-clusters`
Triggers cross-source product entity resolution, canonical clustering, and ingredient propagation across the entire catalog.

---

### 4.10. Admin Observatory & Forensic Telemetry Router (`/admin`)
Provides real-time visibility into scraped raw source data, ingredient token trees, 4-gate INCI matching scores, multi-store pricing tables, and clinical safety ratings.

#### `GET /admin/overview`
Returns high-level system vitals, catalog health metrics, per-store ingestion distributions, and recent background pipeline executions.
- **Example Response:**
  ```json
  {
    "vitals": {
      "total_listings": 22768,
      "canonical_products": 21680,
      "multi_store_clusters": 930,
      "products_with_ingredients": 3493,
      "inci_matched_products": 413,
      "ai_verified_products": 57
    },
    "stores": [
      { "store_id": "sourcebeauty", "store_name": "Source Beauty", "total_products": 7058, "with_ingredients": 1820 },
      { "store_id": "drdawood", "store_name": "Dr Dawood", "total_products": 5691, "with_ingredients": 412 },
      { "store_id": "feel22", "store_name": "Feel22 Egypt", "total_products": 4885, "with_ingredients": 890 },
      { "store_id": "bloompharmacy", "store_name": "Bloom Pharmacy", "total_products": 4613, "with_ingredients": 320 },
      { "store_id": "bashrety", "store_name": "Bashrety", "total_products": 403, "with_ingredients": 41 },
      { "store_id": "rawafrican", "store_name": "Raw African", "total_products": 118, "with_ingredients": 10 }
    ],
    "recent_tasks": [
      { "task_id": "task_20261023_...", "task_name": "full_pipeline_sync", "status": "COMPLETED", "progress_pct": 100 }
    ]
  }
  ```

#### `GET /admin/products?q={query}&store_id={id}&has_ingredients={bool}&ingredient_source={src}&is_multi_store={bool}&gate_status={status}&page={page}&page_size={size}`
Paginated forensic product browser with multi-dimensional filtering.
- **Query Parameters:**
  - `q`: Search keyword across product titles and handles.
  - `store_id`: Filter by specific retail pharmacy (`bloompharmacy`, `drdawood`, `feel22`, `sourcebeauty`, `bashrety`, `rawafrican`).
  - `has_ingredients`: Boolean (`true`/`false`) filtering products with populated ingredient lists.
  - `ingredient_source`: Provenance filter (`store_description`, `cross_store_linked`, `skinsafe_matched`, `curated`).
  - `is_multi_store`: Filter products that have matching listings in other competitor stores.
  - `gate_status`: Filter by INCI gate decision status (`auto_approved`, `pending_gemini`, `rejected`, etc.).
  - `page`: Page index (default 1).
  - `page_size`: Products per page (default 20, max 100).

#### `GET /admin/products/{product_id}/telemetry`
Returns the complete forensic telemetry dossier for a specific product listing:
1. **Source & Scraping:** Raw scraped HTML, source store URL, handle, live inventory status, price, and compare-at price.
2. **Ingredients & Actives Provenance:** Raw ingredient text, parsed normalized tokens, identified active ingredients, and provenance source badges (`store_description`, `cross_store_linked`, `skinsafe_matched`).
3. **4-Gate INCI Telemetry:** Matched reference formulation ID, reference product title, dense vector similarity (ONNX BGE-small), RapidFuzz token match ratio, hybrid score, MiniLM cross-encoder score, Jev/Gemini decision statuses, Gate 1-4 decision logs, and cosmetic safeguard evaluations.
4. **Cross-Store Linking & Price Comparison:** Canonical cluster ID, normalized clustering key, price spread, maximum savings percentage, and all competitor offers with current prices, in-stock badges, best deal indicators, and direct store URLs.
5. **Clinical Dermatological Safety:** Comedogenicity score (0-10 scale), irritancy score (0-10 scale), and any clinical safety warnings or contraindications.
6. **Top 5 Similar Products / Dupes:** Match score %, cross-encoder score %, shared active ingredients, differing active ingredients, formula weighted Jaccard index, and plain-language match rationale.

---

## 5. Data Quality: Title Enrichment & Truncation Remediation

### 4-Tier Remediation Strategy
Implemented in [`pipeline/title_enricher.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/title_enricher.py):
1. **Curated v10 Mapping:** Exact match lookup against `master_product_database_v10_final.csv` full titles.
2. **Handle Qualifier Reconciliation (`reconcile_title_from_handle`):** Extracts missing cosmetic qualifiers, preserves compound attributes (`Aluminum-Free`, `Fragrance-Free`, `Oil-Free`, `Vitamin C`), and filters campaign noise (`-eid`, `-30ml`).
3. **Brand Prefixing on Generic Nouns:** Prefixes brand to titles containing bare cosmetic nouns with normalized alphanumeric deduplication.
4. **Punctuation Sanitization:** Removes trailing hyphens, commas, and ellipses while strictly preserving cosmetic ratings ending in `+` (`SPF50+`, `PA++++`).

### Production Migration Results
- **Total Catalog Products Processed:** 22,768 (crawled across all 6 stores)
- **Titles Enriched & Non-Truncated:** **0 titles ending in ellipsis (`...` or `…`) across all 22,768 products!**
- **Catalog Growth by Store:**
  - `sourcebeauty`: 7,058 products
  - `drdawood`: 5,691 products (newly ingested to full depth)
  - `feel22`: 4,885 products
  - `bloompharmacy`: 4,613 products
  - `bashrety`: 403 products
  - `rawafrican`: 118 products (newly ingested to full depth)
- **Products with Extracted Ingredients:** **3,492 products** (expanded from 3,219 via cross-source store cluster propagation and verified INCI matches)
- **Multi-Store Linked Clusters:** **930 distinct product lines** sold across multiple Egyptian retail sources.

---

## 6. Decoupled AI Verification Workers & Batching

### 1. Jev Worker (`pipeline/jev_worker.py`)
- Evaluates products queued with `gate4_jev_status = 'pending_jev'`.
- On HTTP 429 rate limit, hands off products to `pending_gemini` without data loss.

### 2. Gemini Batch Worker (`pipeline/gemini_worker.py`)
- Evaluates products queued with `gate4_gemini_status = 'pending_gemini'`.
- Consolidates products into batches (default batch size: 10) within a single structured prompt.
- Reduces API calls by 90%, easily sustaining operations under 15 RPM and 1,500 RPD quotas.
- Automatically enriches missing ingredients from matched `inci_reference` records on Gemini approval.
- Preserves scraped store HTML ingredients on candidate rejection.

### 3. Pipeline Job Orchestrator (`pipeline/run_pipeline_job.py`)
- End-to-end multi-process orchestrator supporting 5 automated phases:
  - **Phase 1: Catalog Ingestion & Truncation Remediation** (`harvester.py`)
  - **Phase 2: High-Confidence 4-Gate INCI Formulation Matching** (`inci_matcher.py`)
  - **Phase 3: Decoupled Semantic Cross-Verification** (`jev_worker.py`)
  - **Phase 4: Gemini Batch Verification** (`gemini_worker.py`)
  - **Phase 5: Cross-Source Product Linking & Formulation Propagation** (`product_linker.py`)
- Vector search with FastEmbed ONNX BGE-small embeddings and NumPy 2D BLOB normalization.
- Joint Cross-Encoder reranking (`Xenova/ms-marco-MiniLM-L-6-v2`) calibrated at 0.85 threshold.
- Auto-approves high-confidence matches ($\ge 0.90$ hybrid or $\ge 0.95$ cross-encoder + $\ge 0.80$ hybrid).
- Queues ambiguous candidates ($0.45 \le \text{hybrid} < 0.90$) into SQLite `pending_gemini` queue.

---

## 7. Automated Test Suite & Verification

The test suite contains **66 comprehensive unit and integration tests** verifying all layers of the platform:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

| Test Module | Coverage | Status |
| :--- | :--- | :--- |
| `tests/test_api_operations.py` | Catalog refresh, INCI match, ingredients fill, similarity score, dermo score | **Passed (15/15)** |
| `tests/test_price_comparison.py` | Entity resolution, volume normalization, word-order invariance, price comparison, arbitrage deals, cross-store ingredient propagation | **Passed (16/16)** |
| `tests/test_title_enrichment.py` | Brand detection, SPF preservation, handle reconciliation, API endpoints | **Passed (10/10)** |
| `tests/test_control_tables.py` | Task creation, subtasks, event logging, checkpoints, cancellation | **Passed (8/8)** |
| `tests/test_rotational_logging.py` | File rotation, size triggers, level filtering, log context injection | **Passed (6/6)** |
| `tests/test_admin_router.py` | Admin observatory overview, high-speed multi-filter product search, full-depth forensic telemetry | **Passed (5/5)** |
| `tests/test_workers.py` | Jev worker, Gemini batch processing, queue handoffs | **Passed (4/4)** |
| `tests/test_evaluation_benchmark.py` | SOTA benchmark accuracy, cross-encoder latency | **Passed (2/2)** |
| **Total** | **66 Comprehensive Tests** | **100% OK** |

