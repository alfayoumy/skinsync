# SkinSync workflows and operations runbook

**Current-code operations guide — 25 October 2026.** For exact method/path and payloads, see [API reference](api-reference.md). For persistent records and SQL, see [database reference](database-reference.md). The newer `/admin/workflows/*` operations are **distinct** from older `/products/refresh` and `daily_sync.py` legacy commands; avoid treating them as synonyms.

## Four independent operator workflows

| Workflow | Entry in Admin | API and worker | Database effects | Explicitly does **not** do |
|---|---|---|---|---|
| 1. Discover, classify, deduplicate | Catalog Operations → Add & match products | `POST /admin/workflows/products/discover-multiple` → `catalog_discovery` task; bounded single-source `/products/discover` | Source observations/variants, identity links, pending reviews, provisional canonicals and initial offers | INCI waterfall, separate price-only sweep, model-worker execution |
| 2. Update prices and availability | Catalog Operations → Update prices & stock | `POST /admin/workflows/offers/refresh` | Existing `product_offers` prices/stock + affected canonical aggregates | New products/variants; identity changes; INCI |
| 3. Retrieve INCI | Catalog Operations → Find ingredients | `/admin/waterfall/batch/*` → canonical-only INCI resolver | Verified formula publication, or rejected/candidate audit evidence; optional queue staging | Source discovery, offer refresh, automatically executing Gemini/Jev queues |
| 4. Manage AI queues | Catalog Operations → AI review queues; Product Matching Reviews | INCI `/admin/queues/*` vs. identity `/admin/aggregation/reviews/gemini/*` | INCI queue candidate decisions **or** identity review verdicts and audited identity changes, depending on queue | Assume either type of model verdict proves ingredient provenance |

### Workflow 1A — source ingestion / matching

1. Open **Manage Product Sources** (separate from Product Reviews). Review enabled flags, adapter, source type/market, robots and source-specific policy. Staged registry entries are not necessarily allowed to collect or publish.
2. In **Catalog Operations**, select all enabled sources or a subset. For Shopify select **All pages** (sends `pages=0`) or a finite positive page limit. For other adapters, supply URLs as source-ID/URL pairs, or rely on previously observed URLs; missing URLs yield `needs_urls` rather than fake coverage.
3. Use `dry_run=true` first. Start the tracked job and poll `GET /admin/workflows/products/discover-multiple/{task_id}`. Per-source results include pages scanned, source-specific success/failure, products found, matched identities, provisional products and review cases.
4. A real import checkpoints completed Shopify pages. If stopped, use the reported `next_page` as a manual resume position. **A rate-limit exception is not a successful end-of-catalog signal;** repeating the same Shopify page triggers `repeated_page` rather than completion.
5. Verify source snapshots, canonical visibility, duplicate candidates, classification and offer variants. For third-party HTML pages, robots rules are enforced by the registered-source collector and HTTPS/domain ownership is checked.
6. **Automatic exact legacy identity validation**: `POST /admin/workflows/products/verify-existing` with `dry_run=true` previews all remaining legacy candidates, then `dry_run=false,max_verified=500` applies a bounded safe batch. Legacy conflicts can also be persisted without verifying anything through `POST /admin/workflows/products/prepare-reviews`.

### Workflow 1B — Product Matching Reviews and Gemini

1. Open **Product Matching Reviews**, not Manage Product Sources. If no cases are saved, click **Find legacy products needing review**. A report of "0 new cases; N pending" means the cases already exist.
2. Queue cases with `POST /admin/aggregation/reviews/gemini/enqueue`, optionally supplying `review_ids`, then start `POST /admin/aggregation/reviews/gemini/run`. Default `batch_size=10`, allowed 1–20. Each request evaluates several cases with a **single provider call**.
3. Gemini stores `verdict` (`same_product`, `different_product`, `uncertain`), `confidence` and explanation independently of the resulting application state. The default guarded auto-verification threshold is **0.90**; threshold confidence alone never bypasses brand aliases, identity/variant hard gates, changed evidence, duplicate canonicals or incompatible full INCI.
4. The UI separates **Waiting for Gemini**, **Needs manual review**, and **All unresolved cases**. A confident `different_product` verdict with no safe partition is a **structural issue**, not an instruction to merge.
5. To avoid paying for repeat evaluations, click **Preview saved decisions**, inspect projected verified, split, and held-for-review counts, then click **Apply confident decisions — no Gemini calls**. Backend endpoints: `POST /admin/aggregation/reviews/gemini/saved/preview` and `.../saved/apply`. Replay targets saved cases still marked `needs_manual` (with matching evidence hashes) and ≥0.90 confidence. It cannot create a safe partition from a simple confident verdict alone.
6. When a known explicit variant partition is safe (e.g. sunscreen lotion versus lotion spray, distinct named fragrance/shade/product-line editions), the processor groups **all equivalent retailer listings first**, anchors the original canonical to its existing formulation group, then creates **one new provisional canonical per additional formulation group**—not per retailer listing. Pack sizes (120/200 ml) and sales bundles (1+1) are different offers, not necessarily new formulations. The preview includes every proposed group's offer IDs and total new canonical count. Unknown/conflicting groups remain pending; cannot-link and audit history are preserved. Other cases remain for human review.
7. Use **Gemini Decisions** to audit all saved verdicts, including completed decisions. Filter by verdict, application status, review status, 0–100% confidence bounds, product/canonical/review ID; sort by confidence, review ID, verdict, outcome or time. View separately Gemini's original explanation and SkinSync's publication/identity guard reason. The source is `GET /admin/aggregation/reviews/gemini/results`; it performs **no model calls**.
8. Never confuse the identity Gemini worker with the distinct INCI Jev/Gemini workers under `/admin/queues/*`.

### Workflow 2 — existing stock and price only

Use `POST /admin/workflows/offers/refresh` with a known retailer Shopify source and a positive finite `pages` value. Toggle `update_price` and `update_stock` independently; defaults are both true with `dry_run=true`. The operation updates only existing linked `product_offers` keyed by source product + variant, and validates brand, SKU/GTIN and marketed identity before updating. `unknown_variants_skipped`, `identity_mismatches_skipped` and `offers_updated` explain why an offer was not changed.

**Current limitation:** this endpoint accepts an arbitrarily large *finite* page count but is synchronous; it does not expose a background indefinite `All pages` mode. For initial full retailer discovery use Workflow 1. A legacy `daily_sync.py` run mixes ingestion/prices and has a separate codepath; it is not the guarantee of identity-only/offer-only separation.

### Workflow 3 — verified INCI waterfall

1. Preflight `GET /admin/waterfall/batch/eligibility`; understand `total_skincare`, `in_scope`, `blocked_unverified`, `blocked_pending_review` and `eligible_before_limit`. These are **existing canonical skincare** counts, not new product discovery metrics.
2. Choose `scope=unmatched_only` for items missing approved ingredients; `all_skincare` rechecks verified skincare identities. Identity review/certification belongs in Workflow 1, not a hidden INCI modal step.
3. Use `POST /admin/waterfall/batch/start` with a small `dry_run=true` batch and inspect `GET /admin/waterfall/batch/status`. It must distinguish published formulations, evidence-only outcomes and unresolved products; a zero-target batch means eligibility is zero, not that scraping took place.
4. Ingredient source precedence, ordered tokenization, region, exact source-product link, completeness and version conflict checks live in `FormulationRepository` and `source_formulations.py`. Candidate web-search/INCI database matches, LLM approval or partially listed actives are **not** equivalent to authoritative full INCI.
5. `queue_jev` and `queue_gemini` in the waterfall *stage* work. They do not automatically execute the old INCI workers. Monitor/start/pause/stop with `/admin/queues/status|start|pause|resume|stop`.
6. Use `POST /admin/aggregation/formulations/{canonical_id}/resolve` and the explicitly reviewed evidence-approval APIs for controlled formulation publications/corrections. Restricted formula correction operations need both `formula` and `formula_correction` scopes.

### Workflow 4 — AI queue lifecycle

| Worker | Purpose | API controls | Persistent state |
|---|---|---|---|
| Gemini identity | Product identity review, multiple case evaluations per call | `/admin/aggregation/reviews/gemini/queue`, `enqueue`, `run`, `jobs/{task_id}` and `cancel` | `identity_gemini_queue`, `product_review_queue`, `pipeline_tasks`, `canonical_identity_history` |
| Gemini identity **saved replay** | Apply already stored verdicts, **zero new provider requests** | `/admin/aggregation/reviews/gemini/saved/preview`, `apply` and task endpoints | Review/action and canonical history; 25-case transactional checkpoints |
| Jev INCI | Ingredient/reference matching | `/admin/queues/*` with `worker=jev` | INCI queue and source evidence |
| Gemini INCI | Ingredient/reference matching, separate from identity | `/admin/queues/*` with `worker=gemini` | INCI queue/evidence; never bypass exact-source publication guard |

For any job failure, inspect the task record and logs; do not mark resolved because its queue row has a high confidence alone. Gemini rate use is governed by `api_rate_limits` and `api_request_log` (configuration includes RPM/RPD and batch size).

## Admin access and required configuration

- The API mounts `AdminAuthentication`. Set `SKINSYNC_ADMIN_CREDENTIALS` to a JSON map from administrator names to records with a token of **at least 32 characters** and scoped permissions. Allowed capabilities: `read`, `collection`, `identity`, `formula`, `formula_correction`, `operations`. A correcting formula action needs both `formula_correction` and `formula`. No wildcard scopes.
- Example structure **with placeholders only**:

```json
{
  "catalog_operator": {
    "token": "REPLACE_WITH_A_RANDOM_TOKEN_OF_AT_LEAST_32_CHARS",
    "permissions": ["read", "collection", "identity"]
  },
  "inci_reviewer": {
    "token": "REPLACE_WITH_ANOTHER_RANDOM_32_PLUS_CHAR_TOKEN",
    "permissions": ["read", "formula", "formula_correction"]
  }
}
```

- Put the serialized JSON into the environment variable (do not commit secrets or real tokens). Browser requests attach `Authorization: Bearer ...` via `skinsync-app/src/apiFetch.js` only for `localhost:8000` or `127.0.0.1:8000`. Cross-host deployments require deliberate frontend-origin/auth handling.
- `GEMINI_API_KEY`, `GEMINI_MODEL_NAME`, `GEMINI_MAX_RPM`, `GEMINI_MAX_RPD`, `GEMINI_TIMEOUT_SECONDS`; `OPENCODE_API_KEY`/`JEV_API_KEY`; `LANGSEARCH_API_KEY` for optional providers. For source collectors, `SHOPIFY_PAGE_LIMIT`, `SHOPIFY_MAX_RETRIES`, `SHOPIFY_TIMEOUT_SECONDS`, `SHOPIFY_DEFAULT_DELAY_SECONDS` and per-source `rate_limit_seconds`. See `api/pipeline/config.py` and `api/.env.example`.
- `SKINSYNC_ALLOWED_ORIGINS` determines CORS allowed development origins; wildcard is filtered out. `SKINSYNC_DB_PATH` selects the SQLite file. `LOG_LEVEL`, `LOG_DIR` and rotation keys configure local logs.

## Startup and validation

**Supported local reference environment:** Python 3.12 (the GitHub Actions test interpreter), Node 22 and npm; `api/requirements.txt` is the Python manifest. Run commands from the repository root:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r api/requirements.txt httpx

# Use your configured SKINSYNC_DB_PATH; initialization is an explicit step.
cd api
python -c "from pipeline.db_manager import init_db; init_db(seed_defaults=False)"
uvicorn app:app --host 127.0.0.1 --port 8000
```

In a separate terminal:

```bash
cd skinsync-app
npm ci
npm run dev
# Release checks:
npm run build
npm run lint
```

Backend isolated regression suite (reads source catalog through SQLite backup, then uses a disposable DB and mocked providers):

```bash
python api/tests/run_regressions.py
```

**Container caution:** as checked in October 2026, `api/Dockerfile` still starts from `python:3.9-slim` whereas GitHub Actions exercises Python **3.12**; recent type annotations and code are not guaranteed to work on Python 3.9. Treat container base-image alignment as a deployment prerequisite. Do not equate a green CI run with testing a live upstream store or with successful real Gemini requests.

## Monitoring, recovery and safety

- Jobs: `GET /tasks`, `GET /tasks/{task_id}`, `GET /tasks/{task_id}/subtasks`, `GET /tasks/{task_id}/logs`; individual new workflow jobs also have dedicated status endpoints.
- Shopify scans: stop at page boundary, inspect `last_completed_page`/`next_page`; fail/resume only the affected source and preserve partial committed pages.
- Gemini identity: recover orphaned `processing` rows if their parent task is no longer running; provider/network failures leave cases queued for retry. Check `GEMINI_API_KEY` and quotas without leaking the key into logs.
- Saved Gemini replay: stored evidence-hash mismatch, duplicate canonical, formula conflict or incomplete variant partition blocks automatic writes; check `sample_results` before apply.
- Source rollout: evaluate sample labels, finalize, and run reconciliation preview; never mass-approve identity solely because an LLM confidence score is high.
- INCI: require exact marketed formulation, appropriate regional version, complete ingredient list, provenance, non-truncation and publisher approval. Do not run destructive legacy reclustering just to obtain more INCI-eligible IDs.
- Database: take an online SQLite backup before long imports/replay/retraining. In WAL mode the live `.db` file alone may omit committed transactions from its `-wal` file; prefer Python `sqlite3.Connection.backup` or a controlled `VACUUM INTO`. Check `PRAGMA foreign_key_check` afterward.
- Public route `GET /health` and protected `GET /admin/overview` are quick checks; they do not establish end-to-end provider connectivity. Use `/admin/aggregation/connection-tests` for policy-bound provider/source checks.

## Known implementation boundaries (as of this documentation update)

1. HTML/non-Shopify sources lack a generic all-page discovery adapter; selected sources without URLs yield `needs_urls`.
2. Offer-only price/stock refresh is finite/synchronous, unlike tracked all-page Shopify discovery.
3. The old mixed-purpose `daily_sync.py` and older reclassifier/linker endpoints still exist for compatibility. Prefer the separated workflows for controlled new operations.
4. Product identity Gemini decisions are not a licensed authority for complete INCI provenance. A confident different-product verdict without deterministic, explicit per-offer partition remains structural review.
5. Some historical evaluation and benchmarking documents are preserved as dated baselines, not up-to-date production evidence.
