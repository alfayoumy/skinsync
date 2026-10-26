# Admin V2 — implementation guide

**Branch:** `feature/admin-v2-operations-wizard`
**Status:** Functioning migration increment. Do **not** mistake this for a full rollout of the architecture RFC.
**Safety:** The legacy matching, INCI evidence, identity, source provenance, schema, and backup/restore rules remain in control of all data mutations.

## Start the new admin

Run the existing FastAPI backend and Vite frontend normally. Open Admin Portal and authenticate with an administrator credential. The new V2 shell is the default landing page; the existing catalog explorer and legacy guarded maintenance controls remain accessible from the sidebar.

## Screens shipped

- **Operations Home:** active persisted pipeline tasks, process-local INCI workers, saved workflows.
- **Catalog Build Wizard:** seven steps — source selection, tracked discovery, saved observation rematching, classification review, verified INCI waterfall, distinct INCI queue review, and catalog-quality review.
- **Repair Existing Catalog:** identity through INCI/quality without re-scraping sources.
- **Prices & Stock:** existing offer-only refresh, with page-level committed checkpoints, pause/stop/resume and restart recovery.
- **Job Center:** persisted pipeline tasks, INCI research runs, plus explicitly marked process-local Jev, Gemini and waterfall workers. View task logs, last page and failure metadata.
- **AI & API Operations:** configured provider status (Gemini, Jev, LangSearch, DonSeTch MCP readiness), identity/INCI queue separation, dry-run/saved-verdict batch previews, real backend worker controls, and effective runtime limits. LangSearch local quota controls require typed confirmation.
- **Review Workbench:** separate identity review and INCI formulation-research interfaces.
- **Catalog Explorer / Maintenance:** guarded legacy tools remain available.

## Persistence

Workflow headers and per-step configuration/result/job links are stored in `admin_v2_workflows`, `admin_v2_steps` and `admin_v2_events`. The existing `pipeline_tasks` remains authoritative for task execution. Step status is reconciled with actual job state on read, and advancing the guided wizard requires a completed or explicitly-reviewed checkpoint. **Completing the operator quality review does not automatically publish any ingredients.**

Tracked offer refresh jobs store `next_page`, source, applied update dimensions, requested action, lease and per-page outcome in `admin_v2_offer_jobs`. Worker claims are atomic and a failed or interrupted job can be explicitly resumed after an owner lease expires. Previously committed pages remain; the last uncertain page may be re-executed, but the existing offer updater only updates linked variants and does not create identities or INCI. Pausing or stopping takes effect after the current page, not mid-transaction.

## Semantics and boundaries

- **Save draft:** persistent operator configuration, not a catalog mutation.
- **Preview:** source discovery preview is one real network page per source in dry-run mode; it is not a zero-cost preflight. Offer preview is a tracked dry-run scan. Rematch preview and saved-Gemini replay are read-only, zero provider calls.
- **Apply:** requires explicit confirmation and delegates to existing backend identity/INCI guards. All hard gates remain authoritative even if the UI preview has changed.
- **Job controls:** Jev/Gemini INCI queue workers can pause/resume/stop while their process remains alive. Offer V2 jobs can pause/stop/resume with saved page cursor. Discovery/rematch/saved identity jobs have cooperative stop but not durable pause. The waterfall can stop but cannot yet pause/resume across restarts. INCI research runs are database-backed with stop and per-case retry in the research workbench.
- **Settings:** effective provider settings are visible. Model, API key and runtime limit edits are **read-only** in this increment. A local LangSearch quota reset does not change the remote provider quota.
- **Process restarts:** offer V2 jobs are explicitly recoverable from last committed page. Existing legacy queue and waterfall workers retain process-memory state and are not guaranteed recoverable.
- **Quality:** review-based checkpoints never auto-certify unresolved identities or ingredient formulas. Actual publication still belongs to the guarded legacy formulation operations.

## Known deferred requirements from the target RFC

1. Convert INCI waterfall, identity-Gemini, Jev/Gemini queue workers and discovery to a fully lease-based worker with durable, exactly-once-or-idempotent resume semantics.
2. Add server-side atomic/fingerprinted preview-and-apply orchestration for *every* wizard step. The current V2 saves operational metadata around the existing guarded domain endpoints.
3. Add dedicated, granular classification and formulation QA screens and automated publish manifest. Current classification/quality steps use source-backed read-only audits and human review checkpoints.
4. Offer server-side policy-edit capability with revision audit and safe restart/worker config snapshots. Runtime limits are currently read-only.
5. Migrate legacy overlays to native V2 pages after operator parity tests.
6. End-to-end browser test with actual long-running workers, crash/pause/restart, quota exhaustion, and concurrent operators. API regression tests and Vite build alone do not establish production readiness.

## Tests

The GitHub Actions `Catalog safety regressions` workflow runs isolated backend unittest regressions, Vite production build, and ESLint on the new Admin V2 components. `api/tests/test_admin_v2.py` validates draft persistence, read-only task history, refusal of unsupported control commands, page-checkpointed offer refreshing, and failure/retry behavior without external API requests.

The feature branch intentionally remains a **draft PR** until operational test evidence covers long-running jobs and deployment migration.


## Implemented in PR #18: unified Match Arena (27 October 2026)

The primary `Review Workbench` is now `ManualReviewWorkbench.jsx`, not the two separate large legacy review modals. A dedicated operator may switch **Product identity** / **INCI formulation** and sees a single side-by-side pair with evidence-backed product images and original links, variant/GTIN/SKU/market and source information, proposed full INCI text, provenance and confidence where available. A focused sticky footer exposes exactly three decision controls: **Match**, **No Match**, **Skip**, plus an optional comment. Missing photos and links are explicitly called out rather than invented.

The signed-in reviewer is taken from the server's authenticated actor context on every review action. No user-editable reviewer input is accepted. All choices are checked against a current evidence fingerprint under an SQLite write transaction, with immutable actor/action/comment audit events. Skips are personal; they do not close a pending case. A separate “Skipped by me” view lets the same reviewer return later without requiring a new Gemini call. Discovery's hash-suffixed source variant IDs are resolved back to the exact retained variant, with its own SKU, GTIN, title and identity attributes; unsupported hashes are never reduced to an ambiguous product-level pair. Model suggestions are visible but never treated as proof. Lightweight streak/session points are visual and do not affect decision policy.

**Product identity** quick-review is intentionally restricted to an independently grounded single source listing + one canonical comparator. Match records a reviewed link; No Match records a rejected source/canonical pairing using existing `resolve_review`. Brand, GTIN and explicit identity-attribute conflicts block Match. Multiple plausible canonicals, missing true source evidence and canonical-to-canonical merge cases remain in **Advanced Review Exceptions**. Neither quick decision automatically merges canonical IDs or publishes INCI.

**INCI** quick-review compares canonical identity to retained research evidence; Match delegates to existing `review_case` validation, which checks independently verified identity, unchanged fingerprint, registered source, region, complete ordered INCI and guarded publication. No Match rejects only the proposed evidence; Skip holds the pending case. Out-of-market candidates are blocked from quick Match because market compatibility confirmation cannot be implicit. An actual observed source photo is shown only if linked to the same saved evidence URL; otherwise the UI honestly reports missing imagery.

Endpoints: `GET /admin/v2/manual-reviews?kind=identity|inci` and `POST /admin/v2/manual-reviews/{kind}/{case_id}`, plus `admin_v2_manual_review_events` audit table. Permissions: `read` for listings, `identity` for identity decisions, `formula_correction + formula` for INCI decisions.

**Limitations:** the first 500 pending items are inspected for quick-review eligibility per GET; UI reports a partial scan rather than claiming a complete backlog. Review claiming and complex multi-candidate workflows are deferred. Session points are not persisted. Existing advanced panels remain during migration.

For verification and deployment sequencing, see [Admin V2 next deployment](admin-v2-next-deployment.md). Full architecture scope remains in RFC PR #17.
