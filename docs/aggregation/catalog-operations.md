# Catalog operations: four separate jobs

Open **Admin → Catalog operations**. Each tab has an independent scope:

1. **Add & match products** — scans registered product sources, records original
   product evidence, classifies, and automatically links unambiguous matches.
   New canonical identities are provisional and do not appear as approved products
   until separately published. Ambiguous product identities are queued for review.
   An initial offer may be attached during first ingestion, but no bulk price refresh,
   INCI waterfall, or AI model worker is triggered.
   - Shopify sources: bounded catalog-page import, up to 3 pages per request,
     with explicit starting page. Default is dry-run.
   - Other sources: paste up to 25 product URLs, each on a separate line.
   - **Check existing products automatically** tests older canonical matches
     using exact brand/product identity, hard variant gates, duplicate checks,
     and conflicting INCI evidence. Clear results can be applied in a bounded
     batch without individual human review. Inconsistent identities stay for review.

2. **Update prices & stock** — Shopify only. Updates existing linked variant offers;
   unknown variants and mismatched identities are **skipped**, never imported.
   This never writes canonical INCI or categories. Default is dry-run.
   Use Add & match products for new products or variants.

3. **Find ingredients** — queries *existing* identity-verified skincare canonical
   products, not retailer catalogs. Unverified database/web matches are stored
   as evidence, not authoritative formulas. Select products needing INCI or
   recheck approved skincare identities. A dry-run reports projected publishability,
   not actual database changes.

4. **AI review queues** — explicitly start, pause, or stop Jev and Gemini workers.
   The INCI screen can optionally **stage** uncertain candidates for those queues.
   Staging a queue item does not execute the worker. An LLM match approval does not
   replace the exact-source identity verification required before INCI publication.

The old "Canonical identity eligibility" and "Certify exact matches (max 200)"
controls have been removed from the INCI modal. The previous zero-result preview
was limited to a very restrictive two-retailer matching heuristic; it was not a
catalog-wide measure of true product uniqueness. Bulk identity verification now
belongs to the product workflow.

After installing new code, restart the API and reload the frontend. Start with
a dry-run scan and an identity verification preview; do not assume changes to
GitHub update the running local API or its SQLite database automatically.
