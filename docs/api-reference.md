# SkinSync HTTP API reference

**Verified against:** `api/app.py` and all 12 `api/routers/*.py` modules on `main`, 25 October 2026. All endpoints listed below are code-backed; there are no invented routes. `GET /openapi.json` and `GET /docs` (FastAPI) remain the canonical generated field-level schemas for deployed versions.

## Conventions

- Default local API: `http://127.0.0.1:8000`. Frontend development: `http://127.0.0.1:5173`.
- Admin endpoints and operational reads require `Authorization: Bearer YOUR_ADMIN_TOKEN`; most writes require an explicit capability. The table lists the required capability as evaluated by `security/admin.py`. A `formula_correction` write also requires `formula`.
- GET `/admin/access` reports the authenticated administrator's identity/scopes. Unauthorized requests return 401; missing credentials produce 503; insufficient scopes produce 403.
- Query and body validation is performed by Pydantic/FastAPI (422). Domain checks commonly return 400, 404 or 409. Background starts generally return a task ID, **not** a guarantee the work has succeeded.
- `dry_run=true` is the default for catalog discovery and updates. Product identity verification and saved-decision application have separate explicit preview/apply endpoints.
- IDs must be treated as distinct domains: `products.id` (legacy retailer record), `canonical_products.id` (durable identity), `product_offers.id` (variant-level offer), `product_review_queue.id` (review case), and `pipeline_tasks.task_id` (workflow job).

## Core API recipes

### 1. Discover from all selected source IDs (tracked job)

```http
POST /admin/workflows/products/discover-multiple
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "source_ids": ["sourcebeauty", "feel22", "bloompharmacy"],
  "start_page": 1,
  "pages": 0,
  "dry_run": true,
  "urls_by_source": {}
}
```

`pages=0` means all Shopify pages until an empty page; a positive number caps scanning. The accepted response has `task_id`. Poll `GET /admin/workflows/products/discover-multiple/{task_id}` for per-source results, active page, progress, and outcomes; stop via `POST /admin/workflows/products/discover-multiple/{task_id}/cancel`. Shopify is a page scanner; non-Shopify sources require supplied or previously observed product URLs and are marked `needs_urls` otherwise. The legacy synchronous `POST /admin/workflows/products/discover` requires a **positive finite** page count.

### 2. Update existing retailer offers only

```http
POST /admin/workflows/offers/refresh
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{"source_id":"sourcebeauty","start_page":1,"pages":20,"update_price":true,"update_stock":true,"dry_run":true}
```

Updates existing linked Shopify variant offers; unknown variants and identity mismatches are skipped. Does **not** create identities, offer variants, or INCI. This endpoint currently uses a finite synchronous page range (no indefinite background mode).

### 3. Review and automate legacy identity decisions

```http
POST /admin/workflows/products/verify-existing
Authorization: Bearer IDENTITY_ADMIN_TOKEN
Content-Type: application/json

{"dry_run":true,"max_verified":500}
```

A preview checks remaining legacy canonicals. An apply (`dry_run=false`) commits at most the requested batch and creates review cases for unsuitable clusters. To prepare review cases **without** verifying anything: `POST /admin/workflows/products/prepare-reviews`.

List persisted cases: `GET /admin/aggregation/reviews?view=manual&status=pending&limit=50&offset=0`; the `view` selector can be `manual`, `awaiting_gemini` or `all`.

### 4. Queue Gemini **product identity** reviews

```http
POST /admin/aggregation/reviews/gemini/enqueue
Authorization: Bearer IDENTITY_ADMIN_TOKEN
Content-Type: application/json

{"limit":10000}
```

An optional `review_ids` array targets selected reviews. Check `GET /admin/aggregation/reviews/gemini/queue`. Run queued cases:

```http
POST /admin/aggregation/reviews/gemini/run
Authorization: Bearer IDENTITY_ADMIN_TOKEN
Content-Type: application/json

{"limit":500,"batch_size":10}
```

Poll `GET /admin/aggregation/reviews/gemini/jobs/{task_id}`, and cancel with `POST /admin/aggregation/reviews/gemini/jobs/{task_id}/cancel`. Batches contain 1–20 cases per provider request (the UI offers 5/10/20). **This is separate from the INCI Gemini queue**.

### 5. Replay **saved** Gemini decisions (zero model calls)

```http
POST /admin/aggregation/reviews/gemini/saved/preview
Authorization: Bearer IDENTITY_ADMIN_TOKEN
Content-Type: application/json

{"limit":5000,"confidence_threshold":0.90}
```

Preview provides `stored_decisions_checked`, `automatically_verified`, `automatically_split`, `still_needs_review` and `sample_results`. Apply the same fields with `POST /admin/aggregation/reviews/gemini/saved/apply` and poll `GET /admin/aggregation/reviews/gemini/saved/jobs/{task_id}`. Stop via `POST /admin/aggregation/reviews/gemini/saved/jobs/{task_id}/cancel`. Replay reads saved verdicts, **does not call Gemini**, and retains ambiguity/conflicts for review.

### 6. Search and filter Gemini decisions

```http
GET /admin/aggregation/reviews/gemini/results?verdict=same_product&confidence_min=0.90&confidence_max=1&sort_by=confidence&sort_order=desc&limit=50&offset=0
Authorization: Bearer READ_TOKEN
```

Filters: `verdict` = `same_product|different_product|uncertain`; `outcome` = `queued|processing|auto_resolved|needs_manual`; `review_status` = `pending|confirmed`; `confidence_min`/`confidence_max` = floats 0–1; `search` = product/ID text; `sort_by` = `confidence|review_id|updated_at|verdict|outcome`; `sort_order` = `asc|desc`; pagination `limit` 1–200 and `offset` ≥0. Response includes `results`, `total` and per-case canonical/product IDs, model explanation, local application reason, retailer listings, audit decision and evidence-current indicator. Includes completed records.

### 7. INCI waterfall (independent from product identity work)

`GET /admin/waterfall/batch/eligibility?scope=unmatched_only` checks existing verified skincare canonical identities only. `POST /admin/waterfall/batch/start`, `GET /admin/waterfall/batch/status`, and `POST /admin/waterfall/batch/cancel` control the INCI job. Admin `/queues/*` controls *INCI* Jev/Gemini workers; it does not run the product identity Gemini worker. Product identity verification is not equivalent to verified full regional INCI, and external reference matches are stored as evidence unless exact-source publication checks pass.

## Complete endpoint inventory

Source paths and router prefixes below were enumerated from the FastAPI route decorators on `main`. For request/response field validation of every less-common route, use the deployed `/openapi.json`, and consult the linked handler and Pydantic request models. A historical architecture document is not a reliable replacement for these generated schemas.

### admin router

Source: [`api/routers/admin.py`](../api/routers/admin.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/admin/overview` | [`get_admin_overview()`](../api/routers/admin.py#L56) | `read` |
| GET | `/admin/brands` | [`list_admin_brands()`](../api/routers/admin.py#L158) | `read` |
| GET | `/admin/products` | [`list_admin_products()`](../api/routers/admin.py#L178) | `read` |
| GET | `/admin/products/{product_id}/telemetry` | [`get_product_telemetry_forensics()`](../api/routers/admin.py#L320) | `read` |
| GET | `/admin/products/{product_id}/alternatives` | [`get_product_alternatives_endpoint()`](../api/routers/admin.py#L657) | `read` |
| PATCH | `/admin/products/{product_id}/classification` | [`update_product_classification()`](../api/routers/admin.py#L695) | `identity` |
| POST | `/admin/pipeline/trigger-linker` | [`trigger_product_linker()`](../api/routers/admin.py#L745) | `identity` |
| POST | `/admin/pipeline/trigger-classifier` | [`trigger_catalog_classifier()`](../api/routers/admin.py#L753) | `identity` |
| GET | `/admin/whitelist` | [`get_whitelist_ingredients()`](../api/routers/admin.py#L765) | `read` |
| POST | `/admin/whitelist` | [`create_whitelist_ingredient()`](../api/routers/admin.py#L826) | `operations` |
| PUT | `/admin/whitelist/{ingredient_id}` | [`update_whitelist_ingredient_endpoint()`](../api/routers/admin.py#L864) | `operations` |
| DELETE | `/admin/whitelist/{ingredient_id}` | [`delete_whitelist_ingredient_endpoint()`](../api/routers/admin.py#L899) | `operations` |
| POST | `/admin/whitelist/reconcile-pending` | [`reconcile_pending_products_with_whitelist()`](../api/routers/admin.py#L917) | `identity` |
| GET | `/admin/waterfall/batch/eligibility` | [`get_waterfall_batch_eligibility()`](../api/routers/admin.py#L1234) | `read` |
| POST | `/admin/waterfall/identity-certification` | [`certify_waterfall_identities()`](../api/routers/admin.py#L1251) | `identity` |
| POST | `/admin/waterfall/batch/start` | [`start_waterfall_batch_endpoint()`](../api/routers/admin.py#L1270) | `formula` |
| GET | `/admin/waterfall/batch/status` | [`get_waterfall_batch_status_endpoint()`](../api/routers/admin.py#L1296) | `read` |
| POST | `/admin/waterfall/batch/cancel` | [`cancel_waterfall_batch_endpoint()`](../api/routers/admin.py#L1304) | `formula` |
| POST | `/admin/waterfall/resolve` | [`run_waterfall_endpoint()`](../api/routers/admin.py#L1316) | `formula` |
| POST | `/admin/products/{product_id}/waterfall-resolve` | [`resolve_single_product_waterfall_endpoint()`](../api/routers/admin.py#L1344) | `formula` |
| GET | `/admin/products/{product_id}/tier-resolutions` | [`get_product_tier_resolutions_endpoint()`](../api/routers/admin.py#L1387) | `read` |
| POST | `/admin/products/{product_id}/execute-all-tiers` | [`execute_all_tiers_endpoint()`](../api/routers/admin.py#L1423) | `formula` |
| POST | `/admin/products/{product_id}/apply-tier` | [`apply_tier_resolution_endpoint()`](../api/routers/admin.py#L1456) | `formula_correction + formula` |
| GET | `/admin/langsearch/quota-status` | [`get_langsearch_quota_endpoint()`](../api/routers/admin.py#L1490) | `read` |
| POST | `/admin/langsearch/set-quota-exhausted` | [`set_langsearch_quota_exhausted_endpoint()`](../api/routers/admin.py#L1499) | `operations` |
| POST | `/admin/langsearch/reset-quota` | [`reset_langsearch_quota_endpoint()`](../api/routers/admin.py#L1511) | `operations` |
| GET | `/admin/apis/status` | [`get_all_apis_status_endpoint()`](../api/routers/admin.py#L1521) | `read` |
| GET | `/admin/queues/status` | [`get_queue_status()`](../api/routers/admin.py#L1659) | `read` |
| POST | `/admin/queues/start` | [`start_queue()`](../api/routers/admin.py#L1665) | `formula` |
| POST | `/admin/queues/pause` | [`pause_queue()`](../api/routers/admin.py#L1680) | `formula` |
| POST | `/admin/queues/resume` | [`resume_queue()`](../api/routers/admin.py#L1686) | `formula` |
| POST | `/admin/queues/stop` | [`stop_queue()`](../api/routers/admin.py#L1692) | `formula` |
| GET | `/admin/identity/audit` | [`get_identity_audit()`](../api/routers/admin.py#L1708) | `read` |
| POST | `/admin/identity/audit` | [`record_identity_audit()`](../api/routers/admin.py#L1719) | `identity` |
| GET | `/admin/identity/reviews` | [`list_identity_reviews()`](../api/routers/admin.py#L1732) | `read` |
| POST | `/admin/identity/reviews/{review_id}` | [`decide_identity_review()`](../api/routers/admin.py#L1755) | `identity` |

### aggregation router

Source: [`api/routers/aggregation.py`](../api/routers/aggregation.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/admin/aggregation/sources` | [`sources()`](../api/routers/aggregation.py#L81) | `read` |
| PATCH | `/admin/aggregation/sources/{source_id}` | [`enable_collection()`](../api/routers/aggregation.py#L87) | `collection` |
| POST | `/admin/aggregation/sources/{source_id}/ingest` | [`ingest()`](../api/routers/aggregation.py#L96) | `collection` |
| POST | `/admin/aggregation/sources/{source_id}/collect` | [`collect()`](../api/routers/aggregation.py#L109) | `collection` |
| GET | `/admin/aggregation/sources/{source_id}/coverage` | [`coverage()`](../api/routers/aggregation.py#L115) | `read` |
| POST | `/admin/aggregation/sources/{source_id}/rollout` | [`rollout()`](../api/routers/aggregation.py#L121) | `identity` |
| GET | `/admin/aggregation/observations` | [`observations()`](../api/routers/aggregation.py#L127) | `read` |
| GET | `/admin/aggregation/reviews` | [`reviews()`](../api/routers/aggregation.py#L138) | `read` |
| GET | `/admin/aggregation/reviews/gemini/queue` | [`gemini_identity_queue_status()`](../api/routers/aggregation.py#L183) | `read` |
| POST | `/admin/aggregation/reviews/gemini/enqueue` | [`gemini_identity_enqueue()`](../api/routers/aggregation.py#L196) | `identity` |
| POST | `/admin/aggregation/reviews/gemini/run` | [`gemini_identity_run()`](../api/routers/aggregation.py#L206) | `identity` |
| GET | `/admin/aggregation/reviews/gemini/jobs/{task_id}` | [`gemini_identity_job_status()`](../api/routers/aggregation.py#L236) | `read` |
| POST | `/admin/aggregation/reviews/gemini/jobs/{task_id}/cancel` | [`gemini_identity_job_stop()`](../api/routers/aggregation.py#L248) | `identity` |
| POST | `/admin/aggregation/reviews/gemini/saved/preview` | [`preview_saved_gemini()`](../api/routers/aggregation.py#L266) | `identity` |
| POST | `/admin/aggregation/reviews/gemini/saved/apply` | [`start_saved_gemini_application()`](../api/routers/aggregation.py#L284) | `identity` |
| GET | `/admin/aggregation/reviews/gemini/saved/jobs/{task_id}` | [`saved_gemini_job_status()`](../api/routers/aggregation.py#L310) | `read` |
| POST | `/admin/aggregation/reviews/gemini/saved/jobs/{task_id}/cancel` | [`stop_saved_gemini_application()`](../api/routers/aggregation.py#L323) | `identity` |
| GET | `/admin/aggregation/reviews/gemini/results` | [`gemini_identity_results()`](../api/routers/aggregation.py#L337) | `read` |
| POST | `/admin/aggregation/reviews/{review_id}` | [`review()`](../api/routers/aggregation.py#L408) | `identity` |
| POST | `/admin/aggregation/brands/aliases` | [`brand_alias()`](../api/routers/aggregation.py#L414) | `identity` |
| POST | `/admin/aggregation/identity/constraints` | [`cannot_link()`](../api/routers/aggregation.py#L420) | `identity` |
| POST | `/admin/aggregation/identity/merge` | [`merge()`](../api/routers/aggregation.py#L434) | `identity` |
| POST | `/admin/aggregation/identity/{canonical_id}/split` | [`split()`](../api/routers/aggregation.py#L440) | `identity` |
| POST | `/admin/aggregation/identity/history/{history_id}/reverse` | [`reverse()`](../api/routers/aggregation.py#L446) | `identity` |
| GET | `/admin/aggregation/identity/history` | [`history()`](../api/routers/aggregation.py#L452) | `read` |
| POST | `/admin/aggregation/reconcile` | [`reconcile()`](../api/routers/aggregation.py#L457) | `identity` |
| GET | `/admin/aggregation/formulations/{canonical_id}` | [`preview_formulations()`](../api/routers/aggregation.py#L463) | `read` |
| POST | `/admin/aggregation/formulations/{canonical_id}/resolve` | [`publish_formulations()`](../api/routers/aggregation.py#L469) | `formula` |
| POST | `/admin/aggregation/formulations/evidence/{evidence_id}/approve` | [`approve_evidence()`](../api/routers/aggregation.py#L480) | `formula_correction + formula` |
| POST | `/admin/aggregation/identity/{canonical_id}/publish` | [`publish_identity()`](../api/routers/aggregation.py#L486) | `identity` |
| GET | `/admin/aggregation/connection-tests` | [`connection_test_status()`](../api/routers/aggregation.py#L497) | `read` |
| POST | `/admin/aggregation/connection-tests` | [`run_connection_tests()`](../api/routers/aggregation.py#L503) | `collection` |
| POST | `/admin/aggregation/sources/{source_id}/evaluations` | [`create_source_evaluation()`](../api/routers/aggregation.py#L543) | `identity` |
| GET | `/admin/aggregation/evaluations/{run_id}` | [`evaluation_details()`](../api/routers/aggregation.py#L549) | `read` |
| POST | `/admin/aggregation/evaluations/{run_id}/labels` | [`label_evaluation()`](../api/routers/aggregation.py#L559) | `identity` |
| POST | `/admin/aggregation/evaluations/{run_id}/finalize` | [`finalize_source_evaluation()`](../api/routers/aggregation.py#L566) | `identity` |
| POST | `/admin/aggregation/reconciliation/previews` | [`reconciliation_preview()`](../api/routers/aggregation.py#L572) | `identity` |
| POST | `/admin/aggregation/reconciliation/previews/{preview_id}/findings` | [`reconciliation_findings()`](../api/routers/aggregation.py#L578) | `identity` |

### catalog router

Source: [`api/routers/catalog.py`](../api/routers/catalog.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| POST | `/products/refresh` | [`refresh_products_endpoint()`](../api/routers/catalog.py#L71) | `collection` |
| POST | `/stores/{store_id}/refresh` | [`refresh_store_endpoint()`](../api/routers/catalog.py#L94) | `collection` |
| POST | `/products/enrich-titles` | [`enrich_product_titles_endpoint()`](../api/routers/catalog.py#L119) | `identity` |

### dermo router

Source: [`api/routers/dermo.py`](../api/routers/dermo.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| POST | `/dermo/score` | [`score_dermo_endpoint()`](../api/routers/dermo.py#L44) | `operations` |
| GET | `/products/{product_id}/dermo-score` | [`score_product_dermo_endpoint()`](../api/routers/dermo.py#L52) | `Public` |
| GET | `/dermo/ingredients` | [`list_clinical_ingredients_endpoint()`](../api/routers/dermo.py#L87) | `Public` |
| POST | `/dermo/ingredients` | [`create_clinical_ingredient_endpoint()`](../api/routers/dermo.py#L92) | `operations` |

### inci router

Source: [`api/routers/inci.py`](../api/routers/inci.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| POST | `/inci/match` | [`match_inci_endpoint()`](../api/routers/inci.py#L195) | `operations` |
| POST | `/products/{product_id}/match-inci` | [`match_product_inci_endpoint()`](../api/routers/inci.py#L229) | `formula` |
| POST | `/inci/batch-match` | [`batch_match_inci_endpoint()`](../api/routers/inci.py#L347) | `formula` |

### ingredients router

Source: [`api/routers/ingredients.py`](../api/routers/ingredients.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| POST | `/products/{product_id}/fill-ingredients` | [`fill_product_ingredients_endpoint()`](../api/routers/ingredients.py#L226) | `formula` |
| POST | `/products/ingredients/batch-fill` | [`batch_fill_ingredients_endpoint()`](../api/routers/ingredients.py#L247) | `formula` |

### logs router

Source: [`api/routers/logs.py`](../api/routers/logs.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/logs/files` | [`list_log_files_endpoint()`](../api/routers/logs.py#L16) | `read` |
| GET | `/logs/recent` | [`get_recent_logs_endpoint()`](../api/routers/logs.py#L27) | `read` |
| POST | `/logs/rotate` | [`rotate_logs_endpoint()`](../api/routers/logs.py#L47) | `operations` |

### prices router

Source: [`api/routers/prices.py`](../api/routers/prices.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/products/{product_id}/price-comparison` | [`compare_product_prices()`](../api/routers/prices.py#L20) | `Public` |
| GET | `/canonical-products/{canonical_id}/offers` | [`get_canonical_offers()`](../api/routers/prices.py#L35) | `Public` |
| GET | `/products/price-deals` | [`get_price_deals()`](../api/routers/prices.py#L49) | `Public` |
| POST | `/products/link-clusters` | [`trigger_product_clustering()`](../api/routers/prices.py#L65) | `identity` |

### recommendations router

Source: [`api/routers/recommendations.py`](../api/routers/recommendations.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/` | [`health_check()`](../api/routers/recommendations.py#L52) | `Public` |
| GET | `/health` | [`health_check()`](../api/routers/recommendations.py#L53) | `Public` |
| POST | `/onboarding` | [`save_onboarding()`](../api/routers/recommendations.py#L64) | `operations` |
| GET | `/user` | [`get_user()`](../api/routers/recommendations.py#L81) | `Public` |
| POST | `/recommend` | [`recommend()`](../api/routers/recommendations.py#L89) | `operations` |
| GET | `/search` | [`search_products()`](../api/routers/recommendations.py#L106) | `Public` |
| GET | `/stats` | [`catalog_stats()`](../api/routers/recommendations.py#L125) | `Public` |
| POST | `/admin/sync` | [`trigger_sync()`](../api/routers/recommendations.py#L172) | `collection` |
| POST | `/admin/verify/jev` | [`trigger_jev_verification()`](../api/routers/recommendations.py#L189) | `formula` |
| POST | `/admin/verify/gemini` | [`trigger_gemini_verification()`](../api/routers/recommendations.py#L206) | `formula` |
| GET | `/synonyms` | [`list_synonyms()`](../api/routers/recommendations.py#L219) | `Public` |
| POST | `/synonyms` | [`create_or_update_synonym()`](../api/routers/recommendations.py#L224) | `identity` |

### similarity router

Source: [`api/routers/similarity.py`](../api/routers/similarity.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| POST | `/similarity/score` | [`score_similarity_endpoint()`](../api/routers/similarity.py#L166) | `operations` |

### tasks router

Source: [`api/routers/tasks.py`](../api/routers/tasks.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/tasks` | [`list_pipeline_tasks_endpoint()`](../api/routers/tasks.py#L30) | `read` |
| GET | `/tasks/{task_id}` | [`get_pipeline_task_endpoint()`](../api/routers/tasks.py#L56) | `read` |
| GET | `/tasks/{task_id}/subtasks` | [`get_pipeline_subtasks_endpoint()`](../api/routers/tasks.py#L76) | `read` |
| GET | `/tasks/{task_id}/logs` | [`get_pipeline_task_logs_endpoint()`](../api/routers/tasks.py#L95) | `read` |
| POST | `/tasks/{task_id}/cancel` | [`cancel_pipeline_task_endpoint()`](../api/routers/tasks.py#L116) | `operations` |
| GET | `/scraping/checkpoints` | [`list_scraping_checkpoints_endpoint()`](../api/routers/tasks.py#L139) | `Public` |
| GET | `/scraping/checkpoints/{store_id}` | [`get_store_scraping_checkpoint_endpoint()`](../api/routers/tasks.py#L149) | `Public` |

### workflows router

Source: [`api/routers/workflows.py`](../api/routers/workflows.py). 

| Method | Path | Handler | Permission |
|---|---|---|---|
| POST | `/admin/workflows/products/discover` | [`discover()`](../api/routers/workflows.py#L234) | `collection` |
| POST | `/admin/workflows/offers/refresh` | [`refresh_existing_offers()`](../api/routers/workflows.py#L239) | `collection` |
| POST | `/admin/workflows/products/verify-existing` | [`verify_existing_products()`](../api/routers/workflows.py#L248) | `identity` |
| POST | `/admin/workflows/products/discover-multiple` | [`discover_multiple()`](../api/routers/workflows.py#L354) | `collection` |
| GET | `/admin/workflows/products/discover-multiple/{task_id}` | [`discovery_progress()`](../api/routers/workflows.py#L383) | `read` |
| POST | `/admin/workflows/products/prepare-reviews` | [`prepare_legacy_review_cases()`](../api/routers/workflows.py#L397) | `identity` |
| POST | `/admin/workflows/products/discover-multiple/{task_id}/cancel` | [`cancel_discovery()`](../api/routers/workflows.py#L413) | `collection` |

### catalog_reset router

Source: [`api/routers/catalog_reset.py`](../api/routers/catalog_reset.py).

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/admin/workflows/catalog/reset/preview` | `reset_preview()` | `read` |
| POST | `/admin/workflows/catalog/reset` | `reset_catalog()` | `operations` |

The preview returns `can_reset`, product-derived table row counts, active jobs, preserved-table counts, and a fingerprint without modifying data. The reset accepts `{"fingerprint":"<64-character preview fingerprint>","confirmation":"RESET ALL PRODUCT DATA"}`, creates and checks a private SQLite backup before mutating rows, refuses active/background jobs, and transactionally purges dependent catalog records. The reset response returns the backup path and deleted record counts. Saved Gemini verdicts remain in the backup, not in the new live catalog.

### selective catalog rebuild router

Source: [`api/routers/catalog_reset.py`](../api/routers/catalog_reset.py).

| Method | Path | Handler | Permission |
|---|---|---|---|
| GET | `/admin/workflows/catalog/selective-rebuild/preview` | `selective_rebuild_preview()` | `read` |
| POST | `/admin/workflows/catalog/selective-rebuild` | `selective_rebuild()` | `operations` |
| GET | `/admin/workflows/catalog/selective-rebuild/jobs/{job_id}` | `selective_rebuild_job()` | `read` |

The selective preview chooses the latest multi-source discovery, checks that it is persisted and all catalogs reached their end, inspects stored-source coverage and returns a fingerprint. POST accepts `{"fingerprint":"...","discovery_task_id":"...","confirmation":"REBUILD FROM SAVED DISCOVERY"}` and schedules a backup-first atomic offline rebuild. Poll the returned job ID for the outcome. Unlike the full reset, source observations and their variants survive and are replayed to rebuild canonical products, offers and legacy-compatible source listings without retaining old INCI or identity approvals.

### Optional INCI source-research queue (DonSeTch MCP + Gemini Flash-Lite)

Unlike the identity matching Gemini queue or the Tier 7 SkinSAFE verification queue, this workflow **researches a missing ingredient declaration**. It runs only after the standard waterfall has failed to publish a verified formula and the operator selected `queue_inci_research=true` on `POST /admin/waterfall/batch/start` or `POST /admin/waterfall/resolve`. The waterfall queues cases only: no research model calls run during matching.

| Method | Endpoint | Capability | Meaning |
|---|---|---|---|
| GET | `/admin/inci-research/connection` | `read` | Whether DonSeTch executable and Gemini key are configured; never exposes key |
| GET | `/admin/inci-research/queue` | `read` | Cases, statuses, confidence components, source evidence and pagination |
| POST | `/admin/inci-research/queue` | `formula` | Manually stage canonical IDs still missing verified INCI |
| POST | `/admin/inci-research/queue/{canonical_id}/retry` | `formula` | Explicitly retry failed/rejected/no-result case |
| POST | `/admin/inci-research/run` | `formula` | Start a bounded, separately triggered source-research job |
| GET | `/admin/inci-research/runs/{run_id}` | `read` | Poll job progress and outcome |
| POST | `/admin/inci-research/runs/{run_id}/cancel` | `formula` | Ask to stop between products |
| POST | `/admin/inci-research/queue/{canonical_id}/review` | `formula_correction` and `formula` | Explicit approve/reject with rationale and publication gates |

Example run request: `{"limit":10,"max_pages":3,"min_confidence":0.85}`. Only registered HTTPS retailer/manufacturer/reference domains are fetched, and the worker makes at most `max_pages` fetches per product. `min_confidence` is a **review-priority threshold**, not an auto-publication threshold. Scores combine model-reported confidence, exact product identity overlap and registered-source assessment. They are not calibrated correctness probabilities.

Results with a complete INCI string literally present in the source page and passing deterministic product-variant and tokenizer gates go to `needs_review` and `inci_audit_evidence` as **unverified evidence**, never to published product ingredients. Reviewers must open the original URL, verify the formulation/market and provide an explanation. An explicit market-compatibility acknowledgement is needed for non-Egypt/MENA sources. Corrections to an existing approved formula require an explicitly opted-in correction and elevated permission.

Statuses: `queued`, `processing`, `needs_review`, `no_result`, `error`, `approved`, `rejected`.


### Audited orphaned task-record reconciliation

| Method | Endpoint | Permission |
|---|---|---|
| GET | `/admin/workflows/catalog/stale-tasks/preview` | `read` |
| POST | `/admin/workflows/catalog/stale-tasks/reconcile` | `operations` |

The preview is read-only and classifies `pipeline_tasks.status='running'` rows into `candidates` (at least 6 hours old and matching very specific known terminal stages: `refresh_catalog`/`refresh_store` after `finished_store_...`; `batch_match_inci`/`batch_fill_ingredients` after `processed_item_N_of_N`; or `api_*_test` task artifacts), and `ineligible_running` (all other, live, unknown, recent, or discovery work). Cancelled-but-active discovery is always protected by the existing reset preflight. The preview returns a fingerprint of **all** running tasks and an active-worker and maintenance check.

Only after an operator independently verifies no underlying worker is alive, POST `{"fingerprint":"...","confirmation":"RECONCILE STALE TASK RECORDS","workers_checked":true}` to atomically mark *just the eligible records* failed, preserve original task stage/logs and store each prior row in `catalog_task_reconciliation_audit`. The endpoint rejects invalid fingerprints, active AI workers or catalog maintenance. No actual workers are cancelled, no product/observation/ingredient/identity rows are changed and no task history is deleted. Refresh the selective rebuild preview afterward.

### Post-rebuild identity readiness and Gemini queue safeguards

`GET /admin/waterfall/batch/eligibility` includes identity-status breakdown and count (capped at 1,000) of independent, exact cross-store certification candidates. New `provisional` identities remain INCI-blocked unless independently certified or reviewed.

`POST /admin/waterfall/identity-certification` with `{"apply":false,"limit":200}` previews up to 200 nonconflicting fresh or legacy identities with at least two independent retailer listings and exact safe variant/profile agreement; set `apply:true` to explicitly certify only eligible identities. No ingredient lists or old Gemini decisions are approved by certification.

`GET /admin/aggregation/reviews/gemini/queue` now includes `readiness.queued_actionable` and `readiness.queued_without_comparable_evidence`, a read-only assessment of queued case evidence. `POST /admin/aggregation/reviews/gemini/retire-unresearchable` (identity capability) moves only unsupported queued cases to `needs_manual` with an explicit explanation and `model_name=NULL`. It makes **zero Gemini provider calls**, does not delete review records, and leaves already evaluated or approved decisions alone. New `/reviews/gemini/enqueue` calls skip unsupported cases rather than charging the model for ungrounded reviews.

- `/admin/aggregation/reviews/gemini/retire-unresearchable`: POST, identity scope, explicitly retires unsupported queued reviews to manual review without Gemini calls.

### Recover previously unmatched product observations (no external lookup)

| Method | Endpoint | Permission |
|---|---|---|
| GET | `/admin/workflows/products/recover-unmatched/preview` | `read` |
| POST | `/admin/workflows/products/recover-unmatched` | `identity` |
| GET | `/admin/workflows/products/recover-unmatched/jobs/{task_id}` | `read` |

Preview enumerates saved Shopify observations referenced by outstanding *null-canonical* identity/category review records whose original identity decision was **unknown brand** or **new identity**, with zero competing candidates. Other missing-brand, conflicting GTIN, ambiguous candidates, unmatched legacy/review cases are not eligible. Start `{"limit":200}` to recover at most 200 distinct source observations (1–500 supported); poll the background task for progress and failure counts.

For each eligible observation, reparse its immutable Shopify payload using the same source adapter as ingestion, register an **unverified exact vendor label** where not previously known, run normal canonical identity resolution and offer linking, and mark obsolete no-comparison review records confirmed with an explicit `catalog_recovery` attribution. Existing retailer listing/offer pointers are attached where uniquely identified. A separate anchored category review is preserved if classification is still uncertain. On any re-evaluation conflict, the per-observation savepoint rolls back and the old review stays unresolved. No external HTTP, Gemini, scraped INCI publication, source observation replacement or full catalog reset occurs. This does not certify that a newly created provisional identity is independently verified.

| GET | `/admin/workflows/products/recover-unmatched/diagnose` | `read` | Preview up to 10 saved-product recovery attempts using per-product savepoints and roll back every attempted change; returns grouped failure stages, types and sampled observation IDs. |

Recovery background jobs now use `failed` when all attempts roll back, `completed_with_errors` if only some succeed, and `completed` only on zero failures. Job metadata includes `failure_reasons` and `failure_samples`; a processed count is never presented as a successful recovery count. No secrets or raw source payloads are included in diagnostics.

### Managed catalog backup inspection and restart-time restore

| Method | Endpoint | Access |
|---|---|---|
| GET | `/admin/workflows/catalog/backups` | `read` |
| GET | `/admin/workflows/catalog/backups/{filename}/preview` | `read` |
| POST | `/admin/workflows/catalog/restore` | `operations` |
| POST | `/admin/workflows/catalog/restore/cancel` | `operations` |
| POST | `/admin/workflows/products/discovery-probe` | `collection` |

Backups are full SQLite copies from the catalog reset or selective rebuild, managed within `SKINSYNC_CATALOG_BACKUP_DIR` (default `<db-directory>/backups/catalog`). The list endpoint enumerates only backup names generated by the existing backup helper, not arbitrary server files. Preview verifies `integrity_check` and foreign keys, required SkinSync tables, shows historic product, canonical, observations, offers, Gemini, and INCI counts, returns a SHA-256 fingerprint, and checks live job and maintenance state.

For an operator-approved restore, `POST /admin/workflows/catalog/restore` with `{"filename":"catalog-YYYYMMDDTHHMMSSZ-xxxxxxxx.sqlite3","sha256":"...","confirmation":"RESTORE CATALOG BACKUP"}` schedules a restore for the **next backend restart**. It does not replace a live open database. The scheduler stores a durable, private manifest and holds the catalog maintenance lock; stop and restart the single SkinSync backend. During import, before catalog caches initialize, the backend obtains a process-exclusive file lock, re-verifies the selected backup hash/integrity, copies the current live database to another rescue archive, restores the selected snapshot with SQLite backup API, validates database integrity, clears the restored maintenance lock, and stores an auditable restore record. The on-disk manifest remains if restoration fails, preventing the API from starting against an uncertain catalog. Use `POST /admin/workflows/catalog/restore/cancel` to revoke a pending request *before* restart. The restore replaces the **entire** database, including source observations, product IDs, pricing and decisions; later live work is not merged back.

The diagnostic `POST /admin/workflows/products/discovery-probe` body is `{"source_id":"bashrety","page":2}`. It fetches exactly one public Shopify JSON page (one retry attempt) and returns a precise reachability report without changing product data. The strict discovery worker also preserves the final HTTP failure reason instead of treating 403, 429, HTML challenge or network failure as an empty catalog.

### Read-only post-restore catalog baseline and data-quality audit

GET `/admin/workflows/catalog/quality-audit` (read scope) reports `restore.status` (`restart_required`, `restored`, `restore_record_unconfirmed`, or `no_recent_restore`), `restore.verified_completed`, the last restore audit record, complete SQLite `quick_check` and FK integrity status, live catalog counts and current-minus-backup count differences. An audit-logged restore can remain correctly verified even when later product writes change counts. The restore is not complete if a pending restore manifest still exists.

Quality report includes per-retailer historical listings, distinct saved source products and active variant offers; canonical identity and category state counts; outstanding review type counts; and read-only risk indicators for identity-unverified products holding published ingredient text, untrusted historical scrape formulas, verified skincare missing full INCI, category uncertainty, same-brand/title collisions, verified GTIN shared by multiple canonicals, missing legacy canonical assignments, approved formulas without approval evidence, and differing complete ordered source declarations. **Indicators are review signals, never proof of duplication or incorrect formula; no products are removed, rewritten, merged or automatically approved.**

### Durable active discovery and unsupported-identity suggestion triage

`GET /admin/workflows/products/discover-multiple/active` (read scope) lists currently running and pending source-discovery tasks from persistent `pipeline_tasks`. A cancelled task remains listed as `cancel_requested` until `current_stage=worker_stopped`; its owner can call `POST /admin/workflows/products/discover-multiple/{task_id}/cancel` (collection scope) even after navigating away. A second discovery job cannot be started while one is active or stopping; this check occurs under the task creation write transaction, returning HTTP 409 with the active ID.

`GET /admin/aggregation/reviews/weak-suggestions/preview` (read scope) returns an evidence fingerprint and number of old unevaluated weak, single-candidate cases that can be classified as **awaiting stronger identity evidence**, not manual disputes. `POST /admin/aggregation/reviews/weak-suggestions/defer` (identity scope) requires the unchanged preview fingerprint and `confirmation="DEFER WEAK MATCH SUGGESTIONS"`; it does not modify source observations, product/canonical identities, INCI or source prices. The status is `deferred` in `product_review_queue`; clients can inspect using `GET /admin/aggregation/reviews?view=evidence_needed`. The existing `view=manual` remains reserved for genuine pending review. Conflicts, verified identifiers, high-similarity incomplete variants, reviewed or model-processed cases are explicitly excluded from this automatic evidence triage.

- GET `/admin/workflows/products/discover-multiple/active` — list running and cancellation-pending scans across browser sessions.
- GET `/admin/aggregation/reviews/weak-suggestions/preview` — read-only count and fingerprint of unevaluated, weak one-candidate identity suggestions.
- POST `/admin/aggregation/reviews/weak-suggestions/defer` — confirmed reclassification to retained, evidence-needed queue (no canonical or INCI changes).

### Deterministic rematch of stale identity reviews after database restoration

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/admin/aggregation/reviews/rematch/preview?limit=500` | Count eligible saved product observations, protected cases, concurrency conditions and evidence fingerprint |
| GET | `/admin/aggregation/reviews/rematch/diagnose?limit=5` | Re-evaluate a sample with complete transaction rollback; no provider calls |
| POST | `/admin/aggregation/reviews/rematch/start` | Confirmation `REMATCH SAVED PRODUCTS`, preview fingerprint and limit (1–10000); create a full database backup, rematch saved observations, track outcomes |
| GET | `/admin/aggregation/reviews/rematch/active` | Recent backend-owned rematch task states, including across browser navigations |
| GET | `/admin/aggregation/reviews/rematch/jobs/{task_id}` | Per-task progress and grouped exceptions |
| POST | `/admin/aggregation/reviews/rematch/jobs/{task_id}/cancel` | Request stop after the current saved product, preserving completed commits |

Only *unreviewed* pending/deferred identity cases with a matching **latest** saved source observation and a null canonical are eligible for this automatic replay. Previous uncertain Gemini answers with empty listing arrays are not treated as proof of product identity; prior model results are retained as historical records. Replay is based on saved, immutable source-derived **identity and variant data**, not the stale Gemini prompt, and does not reuse old ingredient text as newly verified source evidence. A fully unsupported fuzzy suggestion with no exact name, strong identifier, or durable link cannot block creating a distinct provisional canonical. Existing source evidence, approved identity decisions, canonical formulas, category reviews and source observations are preserved. If rematching a case cannot resolve the actual identity safely, its transaction is rolled back and its original review remains in place. Successfully rematched old reviews are marked `confirmed` with `superseded_by_saved_rematch` and an explicit system actor; they are **not** marked human-verified or deemed eligible for INCI publication. If the legacy retailer-to-canonical pointer was manually accepted or identity-verified, the pointer is never force-overwritten. Every actual batch creates an additional complete SQLite backup first; no Shopify/Gemini network calls or destructive reset occur.

Exact API route inventory:

- `/admin/aggregation/reviews/rematch/preview`
- `/admin/aggregation/reviews/rematch/diagnose`
- `/admin/aggregation/reviews/rematch/start`
- `/admin/aggregation/reviews/rematch/active`
- `/admin/aggregation/reviews/rematch/jobs/{task_id}`
- `/admin/aggregation/reviews/rematch/jobs/{task_id}/cancel`


### Rematch admission diagnostics (restored task statuses)

`GET /admin/aggregation/reviews/rematch/preview` now additionally returns `active_jobs` (actual running tasks with task ID, type, stage and update timestamp, or a recent cancel still stopping), `active_workers`, `maintenance_locked`, `idle_pending_records`, `old_cancelled_records`, and `orphaned_gemini_processing_rows`. Historic `pending` task rows without a `started_at` are queued work, not active workers; they no longer block rematching. Gemini queue rows whose owner task is terminal are still preserved but no longer masquerade as live model workers. A running task remains blocked regardless of how old its heartbeat is. A cancelled task remains blocking until worker shutdown or a conservative cancellation grace period. The restore/maintenance lock still blocks writes. `POST /admin/aggregation/reviews/rematch/start` checks admission under `BEGIN IMMEDIATE` and returns the actual blocker details on HTTP 409, rather than a generic error.

### Rematch failed 617 times with the same opaque ValueError

The saved source rematch no longer treats a fuzzy match to an **unverified, unsupported canonical profile** as proof that a comparable product exists. If all candidate identities lack real retailer offers, legacy product listings, accepted source links and a published ingredient formula, have no exact name/durable link/strong identifier, and are below 0.85 similarity, the source listing can become its own **hidden provisional** product even when the unbacked candidate has a missing SPF/variant attribute. Existing verified canonicals, strong/exact matches, accepted links and already backed product identities remain protected.

`GET /admin/aggregation/reviews/rematch/diagnose?limit=50` (max 100) dry-runs matching the next eligible 50 source observations and **rolls back every attempt**. It reports grouped `error_groups` with specific `reason_code`, and `error_samples` containing the source title, prior candidate canonical title and ID, actual retailer offer count, historical listing count, accepted source-link count, similarity and concrete missing/conflicting attributes. Possible codes include `candidate_has_no_retailer_comparison`, `missing_formulation_or_variant_discriminator`, `competing_variant_conflict`, `conflicting_gtin_or_mismatched_identifier`, `multiple_plausible_canonicals`, `unusable_source_brand`, and `unresolved_candidate_identity`. The same diagnostics appear for failures in actual tracked rematch batches. These read-only diagnostics do not call Gemini, rescrape, merge unrelated identities, modify INCI, or delete saved products.


### Reset stale product-identity Gemini responses and requeue source comparisons

GET `/admin/aggregation/reviews/gemini/requeue/preview` inventories the old identity Gemini queue and recomputes eligible, evidence-backed source/canonical comparisons from saved retailer observations. This preview makes **no provider calls, queue changes, product changes, or ingredient publications**. Inspect `requeueable_review_cases`, `skipped`, `old_queue_count`, `blockers`, `fingerprint` and `can_apply`. Counts may differ from the prior failed-rematch total because an interrupted run can leave additional eligible cases.

POST `/admin/aggregation/reviews/gemini/requeue` requires a matching preview fingerprint and exact confirmation `RESET IDENTITY GEMINI AND REQUEUE`. The operation claims a maintenance lock, creates a **new full SQLite backup**, then atomically removes old rows from **identity_gemini_queue** (including its prior live response columns) and **identity_gemini_source_cases**, and queues the current source-backed unresolved cases. The resulting backup path and counts are reported. No old responses are replayed; their historical records remain in the backup. Existing rematched products, source observations, review decisions, canonical constraints and INCI queues are not reset.

Running Gemini requires the **separate** existing `POST /admin/aggregation/reviews/gemini/run` with a bounded `limit` and `batch_size`. New source-level cases return model verdicts and optional supported canonical suggestions; the Gemini worker **never auto-links them**. Separately, `POST /admin/aggregation/reviews/gemini/saved/preview` and `/saved/apply` can replay a high-confidence saved `same_product` suggestion without provider calls. Source links are applied **only** if the latest saved observation/canonical snapshot, independent second-retailer marketed title or verified GTIN, independently equivalent brand, full available formulation discriminators, hard identity gates and lack of unresolved rival canonical all agree. `uncertain`, `different_product`, lower-confidence, stale, or conflicting cases are not linked. Preview reports `source_verdicts_seen`, `source_verdicts_eligible`, `source_matches_linked` and reasons blocked. This never publishes ingredients, changes prices or merges canonical IDs.
