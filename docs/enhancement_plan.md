# SkinSync Production Architecture & Catalog Pipeline Plan

This document defines the complete production data architecture for **SkinSync**, transitioning the platform from legacy static CSV files into an automated, multi-source, real-time relational data engine with daily price and catalog synchronization, native vector search, deterministic cosmetic safeguards, and AI bioequivalence verification.

---

## 1. System Architecture Diagram

```mermaid
flowchart TD
    subgraph Data Sources ["Multi-Store Shopify Harvesters"]
        S1["Source Beauty\n(6,986 products)"]
        S2["Feel22 Egypt\n(4,636 products)"]
        S3["Bloom Pharmacy\n(4,411 products)"]
        S4["Dr Dawood Pharmacy\n(drdawoodstore.com)"]
        S5["Bashrety\n(bashrety.tv)"]
        S6["Raw African\n(rawafrican.net)"]
    end

    subgraph Pipeline Engine ["Automated Ingestion & Hybrid Enrichment Engine"]
        S1 & S2 & S3 & S4 & S5 & S6 --> HARVEST["Shopify Harvester Engine\n(pipeline/harvester.py)"]
        HARVEST --> INCI["4-Tier Guarded INCI Matcher\n(pipeline/inci_matcher.py)"]
        
        subgraph INCI Matching Engine ["4-Tier Hybrid Vector & Rule Pipeline"]
            VEC["1. FastEmbed ONNX Vector Search\n(BAAI/bge-small-en-v1.5, 384-dim)"]
            SPARSE["2. RapidFuzz Token Matcher\n(Hybrid: 0.55 Dense + 0.45 Sparse)"]
            RULE["3. Rule Compatibility Safeguard\n(pipeline/safeguards.py)"]
            JEV_G["4. Jev 1.13 Free Verifier\n(OpenCode Zen SystemOne)"]
            
            VEC & SPARSE --> RULE --> JEV_G
        end
        
        REF[("SkinSAFE Knowledge Base\n(50,969 Verified INCI Formulations\n+ SQLite Native Binary BLOB Embeddings)")] <--> INCI Matching Engine
        INCI Matching Engine --> CLASSIFY["Category Classifier & Exclusions\n(pipeline/classifier.py)"]
    end

    subgraph Storage Layer ["Production Relational Database (WAL Mode)"]
        CLASSIFY --> DB[("SQLite Database (skincare.db)\n(Products, Price History, Sync Runs, INCI Reference)")]
        DB --> HIST["Price Change Audit\n(Tracks inflation & price changes)"]
    end

    subgraph Serving Layer ["Real-Time Recommendation & AI Engine"]
        DB --> MEM["In-Memory Search & Vector Cache"]
        MEM --> ALGO["Cosmetic Bioequivalence Matcher\n(Active, vehicle, & comedogenic scoring)"]
        ALGO --> JEV["Jev 1.13 Free (OpenCode Zen)\nSystem One Calibrated Scorer"]
        JEV --> API["FastAPI Application\n(app.py - /recommend, /search, /stats, /admin/sync)"]
        API --> UI["React Frontend Web App"]
    end
```

---

## 2. Multi-Store Coverage & Live Probing Status

All primary Egyptian skincare sources have been probed and verified as **Shopify JSON compatible** (`/products.json?limit=250`), allowing reliable headless scraping without HTML parsing fragility:

| Store Name | Domain | Platform | Active Catalog Size | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Source Beauty** | `sourcebeauty.com` | Shopify | **6,986 products** | Integrated |
| **Feel22 Egypt** | `eg.feel22.com` | Shopify | **4,636 products** | Integrated |
| **Bloom Pharmacy** | `www.bloompharmacy.com` | Shopify | **4,411 products** | Integrated |
| **Dr Dawood Pharmacy** | `drdawoodstore.com` | Shopify | **Active store** | Integrated |
| **Bashrety** | `bashrety.tv` | Shopify | **403 products** | Integrated & Seeded |
| **Raw African** | `rawafrican.net` | Shopify | **Active store** | Integrated |
| **Faces Egypt** | `faces.eg` | Salesforce Commerce Cloud | *Requires custom cookies/GeoIP* | *Planned (Phase 2)* |

---

## 3. Relational Database Schema (`skincare.db`)

The flat CSV architecture has been replaced with a high-concurrency SQLite database running in **WAL (Write-Ahead Logging)** mode. This allows background crawlers and vector embedding tasks to write price updates without locking active web read requests.

### Core Tables

1. **`products`**:
   - `id`: Auto-incrementing primary key.
   - `store_id`: Store identifier (`sourcebeauty`, `feel22`, `bloompharmacy`, `drdawood`, `bashrety`, `rawafrican`).
   - `store_product_id`: Shopify external product ID.
   - `handle`: Shopify slug for canonical URLs.
   - `title`: Clean product title.
   - `brand`: Verified brand name (zero nulls).
   - `category`: Standardized category (`Serum`, `Cleanser`, `Moisturizer`, `Sunscreen`, `Toner`, `Lip Care`, etc.).
   - `price`: Current price in EGP (`variants[0].price`).
   - `compare_at_price`: Original price before discount.
   - `is_available`: In-stock flag (1 for available, 0 for out of stock).
   - `image_url`: Direct high-resolution Shopify CDN image link.
   - `product_url`: Canonical purchase URL.
   - `ingredients_raw`: Raw INCI text.
   - `ingredients_parsed`: JSON array of normalized INCI chemical tokens.
   - `actives`: JSON array of detected therapeutic actives (Hyaluronic Acid, Niacinamide, Retinoids, etc.).
   - `ingredient_source`: Source of ingredients (`store_description`, `skinsafe_matched`, `v10_curated`).

2. **`price_history`**:
   - `id`: Primary key.
   - `product_id`: Foreign key referencing `products.id`.
   - `old_price`: Historical price in EGP.
   - `new_price`: Updated price in EGP.
   - `changed_at`: Timestamp of price change.

3. **`inci_reference`**:
   - Populated with **50,969 global skincare formulations** from SkinSAFE datasets.
   - `embedding`: **`BLOB`** column storing native 384-dimensional FastEmbed float32 vectors as raw C-bytes for sub-millisecond in-database similarity search.

4. **`sync_runs`**:
   - Tracks metrics for every scheduled crawl: `products_fetched`, `products_added`, `products_updated`, `price_changes`, `status`, and `completed_at`.

---

## 4. The 4-Tier Guarded INCI Formulation Matching Engine

To eliminate the critical errors found in legacy matching, the platform utilizes a 4-tier guarded hybrid pipeline in [`pipeline/inci_matcher.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/inci_matcher.py):

```
Store Product Title
        │
        ▼
[Tier 1: Dense ONNX Vector Search] (FastEmbed bge-small-en-v1.5)
        │
        ▼
[Tier 2: Hybrid Ranker] (0.55 Dense Vector + 0.45 Sparse RapidFuzz Token Score)
        │
        ▼
[Tier 3: Rule-Based Compatibility Safeguard] (CosmeticCompatibilityValidator)
  - Drops & bypasses impossible clashes (Hair vs Face, SPF vs Non-SPF, Rinse vs Leave-on)
        │
        ▼
[Tier 4: Jev 1.13 Free Deep Semantic Verifier] (OpenCode Zen SystemOne)
  - Authoritative bioequivalence verdict on ambiguity candidates (70% - 92%)
        │
        ▼
Verified Clean Ingredients Backfilled to Catalog
```

### Why FAISS Was Not Used (Native SQLite Binary Vectors)
Rather than introducing heavy C++ binaries or external vector database servers (Qdrant, Pinecone, FAISS):
* Embeddings are stored as raw float32 bytes (`BLOB`) directly inside `inci_reference.embedding`.
* Because searches are partitioned by brand first, vector matrix dot-products run via **NumPy in $0.044\text{ ms}$** (44 microseconds).
* Memory footprint for all 50,000 vectors is only **76.8 MB**, enabling zero-overhead execution on CPU.
* Missing embeddings are embedded on-the-fly and cached permanently in SQLite.

### The Rule-Based Safeguard Dimensions ([`pipeline/safeguards.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/safeguards.py))
The deterministic safeguard enforces 8 physical and cosmetic boundaries:
1. **Anatomical Target:** Hair vs. Face (*hair mask vs face mask*), Lip vs. Eye/Face, Eye Contour vs. Full Face, Body vs. Face.
2. **Vehicle Phase:** Rinse-off Cleansers/Soaps/Scrubs vs. Leave-on Creams/Serums/Balms; Leave-on Toners vs. Rinse-off Washes.
3. **Viscosity & Texture:** Liquid Serums vs. Emulsion Creams; Oil-Control Fluids vs. Rich Creams; Oil-Free Gels vs. Lipid Lotions.
4. **Sun Protection:** SPF Sunscreens vs. Non-SPF daily balms.
5. **Circadian Regimen:** Day / AM vs. Night / PM / Overnight.
6. **Finish & Pigmentation:** Tinted / BB / CC vs. Untinted / Invisible / Clear.
7. **Brand Sub-Line Collisions:** Bioderma (*Sébium* $\neq$ *Sensibio* $\neq$ *Atoderm*); La Roche-Posay (*Effaclar* $\neq$ *Toleriane* $\neq$ *Anthelios* $\neq$ *Cicaplast*).
8. **Active Chemical Family & Potency:** Glycolic Acid $\neq$ Niacinamide; Retinol 0.1% $\neq$ 0.3%; Reedle Shot $50 \neq 100 \neq 300$.

### Catalog Audit Results
* **Total Candidates Proposed by String Matching:** 226
* **Automatically Blocked by Rule Safeguard:** **58 (25.7% of all proposed matches)**
* **Candidate Bypassing in Action:** When matching `Cicaplast Baume B5+ SPF50`, the engine blocked three non-SPF candidates and automatically retrieved the true SPF50 formulation with chemical UV filters.
* **Database Cleanse:** All 69 legacy contaminated records in `skincare.db` were purged of corrupt ingredient profiles.

---

## 5. Automated Daily Sync & Production Execution

### Running the Sync Pipeline

1. **Via CLI / Cron Job:**
   ```bash
   python3 repo/api/pipeline/daily_sync.py
   ```
   *To run automatically every night at 2:00 AM on Linux, add to `crontab -e`:*
   ```cron
   0 2 * * * cd /mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api && python3 pipeline/daily_sync.py >> /var/log/skincare_sync.log 2>&1
   ```

2. **Via Admin API Endpoint (Non-Blocking):**
   ```bash
   curl -X POST http://127.0.0.1:8000/admin/sync
   ```
   Dispatches a background worker task that updates prices, logs price changes, and reloads the in-memory cache without server restarts.

3. **Monitoring Live Status (`/stats`):**
   ```bash
   curl http://127.0.0.1:8000/stats
   ```
   Returns live counts of active products, stores, price changes detected, and recent crawl runs.

---

---

## 6. Centralized Configuration & Configurable Thresholds ([`pipeline/config.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/config.py))

To prevent brittle hardcoded magic numbers, all thresholds, weights, and timeouts are centralized in `config.py` as typed parameters that can be overridden dynamically via `.env` or system environment variables:

| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| `MIN_INGREDIENT_COUNT` | **3** (Configurable) | Minimum distinct chemical entities required for Gate 1 validation |
| `MIN_INGREDIENT_RAW_LEN` | `12` | Minimum character length of raw ingredient string |
| `MAX_INGREDIENT_RAW_LEN` | `2500` | Maximum character length to truncate raw ingredient blocks |
| `FASTEMBED_MODEL_NAME` | `BAAI/bge-small-en-v1.5` | ONNX CPU vector embedding model (384 dimensions) |
| `DENSE_VECTOR_WEIGHT` | `0.55` | Weight for ONNX semantic vector similarity |
| `SPARSE_TOKEN_WEIGHT` | `0.45` | Weight for RapidFuzz token similarity |
| `TOP_K_CANDIDATES` | `5` | Candidate pool size evaluated through Gates 1, 2, and 3 |
| `AUTO_APPROVE_HYBRID_THRESHOLD` | `0.93` | Score $\ge 0.93$ auto-approved with 0 LLM calls |
| `AMBIGUITY_ZONE_LOW_THRESHOLD` | `0.70` | Candidates between 0.70 and 0.92 marked `pending_jev` |
| `JEV_MODEL_NAME` | `jev-1.13-free` | Model identifier on OpenCode Zen SystemOne |
| `JEV_CONFIDENCE_THRESHOLD` | `0.70` | Minimum Noul confidence threshold for Gate 4 approval |
| `JEV_REQUEST_INTERVAL_SECONDS` | `2.0` | Pacing delay between Gate 4 Jev API requests |
| `JEV_RATE_LIMIT_BACKOFF_SECONDS`| `10.0` | Exponential backoff pause when receiving HTTP 429 |
| `SHOPIFY_PAGE_LIMIT` | `250` | Maximum products per page during Shopify store ingestion |
| `DEFAULT_MAX_PAGES_PER_STORE` | `40` | Default page depth per store during catalog sync |
| `INCI_BACKFILL_BATCH_SIZE` | `50` | Default product batch size for `match_pending_inci.py` |
| `JEV_QUEUE_BATCH_SIZE` | `25` | Default product batch size for `jev_worker.py` |

---

## 7. Implementation Milestones

- [x] **Relational Schema & WAL Mode Migration:** [`repo/api/pipeline/db_manager.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/db_manager.py) initialized [`skincare.db`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/skincare.db).
- [x] **Knowledge Base Seeding:** 50,969 SkinSAFE formulations seeded into `inci_reference`.
- [x] **Store Harvesters:** 6 Shopify-compatible stores integrated with automated pagination and rate-limiting.
- [x] **Legacy Database Sanitization:** Purged 69 tainted records resulting from old fuzzy matching mismatches.
- [x] **Deterministic Rule Safeguard:** Built [`pipeline/safeguards.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/safeguards.py) covering 8 formulation conflict dimensions.
- [x] **Native Vector Hybrid Retrieval:** Integrated `fastembed` ONNX (384-dim BGE-small) with SQLite binary BLOB vectors and candidate bypassing.
- [x] **Decoupled 4-Gate Architecture:** Crawler (0 LLMs) separated from INCI backfill worker and Gate 4 Jev worker.
- [x] **Per-Gate Telemetry Audit:** Added `gate1_quality_status`, `gate2_safeguard_status`, `gate3_active_status`, `gate4_jev_status`, and distinct score columns.
- [x] **Strategy A & Strategy B:** Self-enrichment into `inci_reference` plus Open Beauty Facts live API fallback.
- [x] **Centralized Configuration:** Configurable minimum ingredient entities set to **3**; eliminated all hardcoded pipeline numbers into [`config.py`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/pipeline/config.py) and [`.env`](file:///mnt/windows/Users/AmrAlfayoumy/Downloads/skincare/repo/api/.env).
- [ ] **Full Multi-Store Catalog Crawl:** Execute `daily_sync.py` across all 6 stores to ingest ~16,000 live products and real-time prices.

