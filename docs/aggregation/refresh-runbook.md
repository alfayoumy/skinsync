# Catalog refresh and waterfall release runbook

**Scope:** Code and operator guidance current as of 25 October 2026. Documentation does not certify that production source evaluations, full catalog crawl, model budgets, independent review or authorized INCI publication have run successfully.

## Preflight

1. Confirm `SKINSYNC_DB_PATH` points to the intended database, and create a WAL-consistent backup (`sqlite3.Connection.backup`). Run `PRAGMA foreign_key_check` on a staging copy.
2. Check admin token and permission scopes. `collection` enables source ingestion/offer updates; `identity` approves identity operations and Gemini replay; `formula` and `formula_correction` distinguish INCI publication/correction.
3. Inspect source inventory, enabled flags, market, reliability, robots/access and rate-limit controls. A source listed in registry is not automatically authorized to crawl or publish.
4. Use an independent labeled evaluation of deduplication/classification before source rollout. See [hardening](hardening.md).

## Catalog operations in the correct order

- **Discovery/identity:** `POST /admin/workflows/products/discover-multiple` with `dry_run=true`; use `pages=0` for all Shopify pages, or finite limit. Track per-store results and explicit `needs_urls` on non-Shopify sources. Promote to a live import only with coverage validation.
- **Historical matching:** `POST /admin/workflows/products/verify-existing` preview and bounded apply; real conflicts enter persisted product reviews. Gemini identity review uses its separate queue. **Replay previously stored** decisions without model calls to reduce the manual backlog.
- **Offer prices and stock:** `POST /admin/workflows/offers/refresh`, finite explicit page range, `dry_run=true` first. Verify `unknown_variants_skipped` and `identity_mismatches_skipped` are explainable. It does not import new products.
- **INCI:** `GET /admin/waterfall/batch/eligibility`, then a bounded dry run, then explicit publication approval. Scope is **verified existing skincare canonical identities**. Do not interpret `in_scope` or `eligible_before_limit` as ingestion coverage.
- **INCI LLM workers:** `/admin/queues/*` explicitly; they are separate from Gemini product identity.

## If the waterfall reports 0/0 or zero approved formulas

`GET /admin/waterfall/batch/eligibility` reports `blocked_unverified` and `blocked_pending_review`. A zero eligible count is a source identity readiness issue, not an incomplete crawl. Work through identity matching/reviews separately. A dry run can fetch candidate evidence but **must not** be represented as published authoritative INCI. Verify source-specific complete lists, region/formula version, truncation and exact identity.

## Recovery and diagnostics

- Tasks: `GET /tasks`, `GET /tasks/{task_id}`, `GET /tasks/{task_id}/logs`.
- Source discovery: failures should not be counted as successful catalog end. Resume from `next_page` after investigating retry/quota issues.
- Gemini identity: inspect `/admin/aggregation/reviews/gemini/queue` and `/results`, not the unrelated INCI queue. A stored confident result may be replayed after policy changes; no new token spend required.
- SQLite: do not copy a live WAL DB without backup awareness, and do not re-run legacy reclustering/automatic INCI publisher scripts to force counts.
- Monitor review queue by `kind` and `status`, publication coverage by `canonical_products.identity_status` and confirmed INCI provenance rather than just the presence of legacy text.

## Evidence and release records

The CSV and JSON benchmarks under `docs/aggregation/` are fixed historical sample artifacts. Revalidate source performance, product uniqueness, category precision and formula identity on current data before a large write. Review and record who authorized any high-volume identity split, rollout, or INCI source correction.

[Full operations guide](../workflows-and-operations.md) · [All APIs](../api-reference.md) · [Database](../database-reference.md)
