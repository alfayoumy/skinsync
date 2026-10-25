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
**Historical INCI safety:** Tier 1 no longer loads January 2026 historical CSV ingredient exports. Product-title/URL matching and valid INCI syntax are not proof of formulation identity. Tier 1 accepts only explicitly verified current-page `store_page_tab` ingredients; exact-source observation evidence is checked separately for identity, ordering, completeness and provenance. Previously saved `historical_store_scrape` formulations are eligible for reprocessing, but are not deleted or automatically corrected. A stored conflicting formulation requires audited remediation/rebuild.

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

### Selective offline rebuild from saved discovery

**Preferred after a completed six-source saved discovery.** In Catalog Operations → Add & match products → Selective rebuild from saved discovery, preview the latest tracked catalog discovery. Only a completed multi-source run with `dry_run=false` and a verified Shopify catalog-end signal for every selected source is accepted. The preview compares fetched product counts with distinct saved observations and requires at least 90% coverage. Active tasks and workers block rebuilding.

Execute with `operations` permission, the preview fingerprint and the exact phrase `REBUILD FROM SAVED DISCOVERY`. A private verified SQLite backup is created first. The background job then atomically deletes old legacy products, canonical identities, reviews, offer prices, formulations, old INCI evidence, Gemini decisions, tasks and checkpoints. Fresh retailer-facing compatibility listings (title, price, availability, store URL, category) are projected from the preserved source observations with **no inherited ingredient strings or AI approvals**. Only the **latest stored source observation per source/product** from the completed run's sources and its source variants survive; recorded Shopify raw JSON is replayed through the current adapter and identity gates, without re-scraping or using legacy CSV INCI. Existing source configuration, brands/taxonomy, and ingredient reference data remain. Every payload/hash and variant identity must match; any mismatch or foreign-key violation aborts the whole transaction. New identities remain provisional or undergo normal review; new INCI is evidence-only until separately certified. Gemini decisions survive in the backup only.

The API returns a job ID; poll `/admin/workflows/catalog/selective-rebuild/jobs/{job_id}` for completion, counts, backup path, or rollback reason. Do not start another discovery or scoring run while maintenance is active. **Preview-only discovery results are never persisted and cannot be rebuilt.**

### Guarded Reset Catalog operation

Catalog Operations → Add & match products → **Reset catalog data** exposes a preview and a guarded reset. The preview lists row counts for all product-derived tables, active jobs and preserved reference counts. The reset requires `operations` permission, an unchanged preview fingerprint and the exact phrase `RESET ALL PRODUCT DATA`. It refuses pending/running tasks, actively processing Gemini decisions, in-process Jev/Gemini workers, and cancelled discovery scans until the current page worker exits. A maintenance gate blocks new product ingestions and task creation during the backup and reset.

An automatic private SQLite backup is verified before the transactional delete; the backup path and audited reset receipt are returned. Backups default to `api/backups/catalog/` beside the configured SQLite file, or `SKINSYNC_CATALOG_BACKUP_DIR`. Never commit backups to Git. The reset clears legacy products, canonicals, offers, observations, INCI evidence/versions, identity histories and reviews (including saved Gemini decisions), evaluation artifacts, and catalog tasks/checkpoints. It preserves source registry/configuration, brands, taxonomy, INCI reference, whitelists, ingredient synonyms, clinical ingredients, and API usage/rate limits. Gemini decisions survive **only inside the backup** because original product IDs cannot be trusted after re-ingestion. The current discovery must finish or stop fully before preview can be applied. If other external workers use the DB, quiesce them before resetting.

### Safe clean-catalog rebuild

There is no catalog-wide purge action in the current Catalog Operations UI or `/admin/workflows/*` endpoints. Do not delete the database or product tables while tracked discovery is running. Shopify discovery commits every completed page, and source observations, canonical products, identity links, offers, formulation evidence and saved review decisions are related.

Finish or cancel discovery at a page boundary, take a consistent SQLite backup, and inventory records to retain (source configurations, curated INCI/whitelist/taxonomy, manual approvals and saved Gemini decisions). Build and validate a new initialized database separately, re-ingest product discovery, then run identity reviews and INCI matching; only switch databases after validation. Resetting without explicitly migrating decisions discards saved decisions.

### INCI missing-formula research via DonSeTch MCP + Gemini Flash-Lite

This is a **separate** workflow from Gemini **product identity** matching and Gemini/Jev **Tier 7 candidate verification**. Install the [DonSeTch](https://github.com/dondai44423/donsetch) command on the backend host (for example `npm install -g donsetch`) and make sure `donsetch mcp --supervised` is runnable by the API process. The Python MCP client is in `api/requirements.txt`. Configure `GEMINI_API_KEY` using existing server-side secret management, and optionally set `INCI_RESEARCH_GEMINI_MODEL=gemini-2.5-flash-lite` and `DONSETCH_COMMAND=/absolute/path/to/donsetch`. API keys must not be sent to the frontend. Review DonSeTch's AGPL-3.0 licensing and per-source collection restrictions before deployment.

Open **Find ingredients** → **Full INCI Waterfall Batch Engine**. Enable **Fallback research: DonSeTch MCP + Gemini Flash-Lite** and turn off dry-run to stage only identity-verified products that still lack an approved full formula at the end of normal lookup. No additional API calls are made by staging. Open **INCI research queue** to inspect status, configure the case limit (1–100), page budget (1–5) and high-confidence review threshold, and separately start the research worker. This is a cost-control boundary: Gemini calls happen only after explicit worker start, use the shared Gemini rate limiter and each case is attempted separately.

The research worker invokes DonSeTch's `web_search` and `web_fetch` via MCP for at most the configured number of pages from already registered source domains. Gemini 2.5 Flash-Lite extracts a candidate from the retrieved text. SkinSync requires the ordered full ingredient string to appear literally in the fetched evidence, checks identity and INCI completeness, and records evidence with a source URL, hashed fetched page, raw candidate, model confidence, identity score, source score and overall priority score. It **never automatically publishes** the formula; a reviewer must verify the source and submit an explicit approval, which goes through normal formulation integrity gates. Missing, suspicious or truncated results stay unresolved. The queue has replay-safe statuses, explicit manual retry, progress and stop controls, and is included in catalog reset/backup.

If DonSeTch or the Gemini API key is absent, the UI shows that missing dependency and refuses to start; existing INCI waterfall functionality is unaffected. The DonSeTch binary is not installed just by merging the GitHub commit. Free Google API tier quotas and availability are account-specific. Do not point this at large-scale crawling or bypass blocked sources; use it as a targeted lookup fallback.


### Diagnose inactive pending catalog tasks before a selective rebuild

Ordinary task rows in `pending` state mean work has not started and should not block an offline catalog rebuild. A new product-discovery scan enters `running` when submitted and still blocks; so does any running task, recently cancelled discovery not yet stopped, or active AI worker. The selective preview displays the IDs, types, stages and update times of blocking task records and the number of idle pending tasks. This change does not auto-retire a `running` record merely due to age: verify a stuck task with its status and workers before handling it separately. A preview is read-only.

### Safely unblock false running-task records after saved product discovery

A completed six-source discovery may coexist with older `pipeline_tasks` rows incorrectly left in `running` state by legacy refresh/matching jobs or HTTP test artifacts. **Do not delete tasks, clear all running statuses or reset the catalog.** The selective rebuild preview now offers **Inspect potentially orphaned task records**, which shows candidate records and deliberately excluded running tasks.

Only the exact approved patterns are eligible: `api_*_test` records with no stage; `refresh_catalog` / `refresh_store` records with `finished_store_...`; and `batch_match_inci` / `batch_fill_ingredients` with `processed_item_N_of_N` at the last item. Every candidate must have been idle at least 6 hours. A currently running discovery, recent or partial tasks, unknown task types, and all workers reported active remain protected. This is a conservative **record reconciliation**, not proof of process death, so the operator must separately check backend processes before confirming. Confirm with `RECONCILE STALE TASK RECORDS`, the exact preview fingerprint and worker-inspection checkbox. The transaction marks eligible rows `failed` and audits the old values but never deletes records or alters product data. After applying, the UI refreshes the rebuild preview.

If other, non-eligible `running` tasks remain, investigate them rather than force clearing them.

### Fresh-source product identity before ingredient matching

After a selective rebuild, new canonical products are deliberately `provisional`; no historical identity trust or INCI approvals are inherited. `/admin/waterfall/batch/eligibility` now returns `identity_status_breakdown`, `certifiable_cross_store_preview_count` (maximum 1,000 shown), and the verified/blocked counts. The **Find ingredients** modal exposes this breakdown and controls **Preview exact cross-store certification** / **Certify next 200 safe identities**. Unlike the old legacy-only matching controls, this certification includes fresh `provisional` identities, but requires exact independently corroborated retailer titles/brands, compatible formulation/variant attributes, unique canonical identity, approved category, no pending identity or category conflicts and at least two distinct source stores. Single-source or conflicted items remain provisional. Only a manually applied certification makes these canonicals eligible for the existing waterfall; it does not publish or infer any INCI.

Gemini identity matching now refuses to spend model calls on review cases missing a canonical anchor and complete *current* cross-retailer snapshots. Such cases remain manual identity/category review, while any previously queued unresearchable cases are marked `needs_manual` with explicit `Gemini not called` attribution. The product-identity Gemini deterministic post-verdict gate now accepts `provisional` state only when all its existing independent multi-store and variant safeguards pass. Saved source-observation and ingredient data are not modified by this remediation.

If eligible remains 0 after preview, it means independently certified source identity evidence is currently insufficient; use targeted human verification for single-source/ambiguous products rather than bypassing the publication trust gate or mass-submitting unsupported cases to Gemini.
