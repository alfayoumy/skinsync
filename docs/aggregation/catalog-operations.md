# Catalog operations — operator guide

Open **Admin → Catalog Operations**. Sources are administered in **Manage Product Sources**; review cases are examined in **Product Matching Reviews**. These are independent screens.

## 1. Add & match products

Select one or multiple enabled collection sources. **Select all enabled sources** chooses every currently allowed source.

- Shopify sources: choose **All pages** (continues until an empty page, `pages=0`) or any positive finite number of pages. Per-page and per-source progress, errors, cancellation and resume position are reported by the tracked job.
- Other source adapters: provide 1–25 known URLs for each selected source using source-ID/URL rows, or rely on previously observed URLs. The importer reports `needs_urls` if none exist; it cannot claim whole-site coverage.
- Dry-run is default and does not persist catalog mutations. Real runs persist original observations/variants, match existing canonical identity where independently supported, stage genuinely new provisional canonicals, attach initial retailer offers, and generate review cases for ambiguities.
- **Check existing products automatically** is a separate exact legacy validation operation: preview all remaining historical identities; apply at most the chosen safe batch. The saved count must decrease remaining safe candidates. **Find legacy products needing review** creates review cases, rather than confusing a diagnostic count with queue state.

Endpoints: `POST /admin/workflows/products/discover-multiple`, `GET /admin/workflows/products/discover-multiple/{task_id}`, `POST /admin/workflows/products/discover-multiple/{task_id}/cancel`, `POST /admin/workflows/products/verify-existing` and `POST /admin/workflows/products/prepare-reviews`.

## 2. Update prices & stock

This is **existing-offer only**. Pick an enabled Shopify retailer, a positive finite number of pages, and choose price updates, stock updates or both. Unknown product/variant keys, unapproved brands, incompatible SKU/GTIN or variant attributes are skipped, not imported. Ingredient text and canonical identity are never changed here. Default is preview.

Endpoint: `POST /admin/workflows/offers/refresh`. This flow currently has no indefinite tracked scan; "All pages" belongs to new product discovery.

## 3. Find ingredients

Run the INCI waterfall **only for existing identity-approved skincare products**. Preflight clearly distinguishes in-scope, verified, pending-review and blocked-unverified identity counts. A 0/0 result means nothing qualified for this flow—not that any products were imported. Dry-run is available; candidate evidence does not imply approved INCI publication. Optional Jev/Gemini staging only adds tasks to the **INCI** queue.

Endpoints: `/admin/waterfall/batch/eligibility`, `start`, `status` and `cancel`; canonical formulation operations are under `/admin/aggregation/formulations/*`.

## 4. AI review queues

**INCI** Jev/Gemini workers use `/admin/queues/*`. **Product identity** Gemini uses Product Matching Reviews with its own queue and jobs. They are different. Model identity approval cannot publish full ingredients.

## Product Matching Reviews (separate screen)

Use **Find legacy products needing review** if necessary, then enqueue all/selected identity cases for Gemini. Review only unresolved conflicting cases manually. Gemini analyzes several product cases per call (configured 1–20), records structured verdicts and reasons, and can automatically verify safe high-confidence exact matches under deterministic gates.

Use **Preview saved decisions** → **Apply confident decisions — no Gemini calls** to re-evaluate *existing* Gemini verdicts ≥0.90 and apply safe identity verifications or explicit audited variant splits. It skips stale or ambiguous cases, does not spend tokens and never auto-merges distinct canonical IDs.

**Gemini Decisions table:** paged, server-side verdict/outcome/review status and confidence-range filters; sort on confidence, verdict, review ID, outcome or time; search titles and IDs; expand a row to see original store listings, Gemini reasoning, SkinSync application reason, and saved audited action (even for completed cases).

## Before every release

- Check admin credentials/scope, source policy and correct `SKINSYNC_DB_PATH`.
- Back up SQLite consistently under WAL before any real bulk operation.
- Start with a bounded dry run, review result counts and independent sample cases, then apply and inspect task status/history.
- Do not mistake a successful start HTTP response for task completion.
- For troubleshooting and precise API examples see [operations runbook](../workflows-and-operations.md) and [API reference](../api-reference.md).
