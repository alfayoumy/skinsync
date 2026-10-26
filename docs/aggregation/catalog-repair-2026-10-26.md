# Catalog repair — 26 October 2026

> Historical checkpoints below. The [consolidated handoff](catalog-repair-handoff-2026-10-26.md) supersedes these counts and records later repairs, two still-published quality failures, the unapplied batch 09 and remaining work.

The catalog repair is ongoing. This report records applied changes, not a claim that the whole catalog is correct.

## Latest checkpoint

The live database now contains **7,342 active canonical records**, including **5,944 skincare records**. **692 identities are verified**. **119 ingredient declarations are published**, including **115 skincare declarations**; 113 of those skincare declarations are on currently visible products. Pending work remains: **973 identity cases and 1,091 category cases**. Gemini's provider response confirms a **daily quota** limit. The goal remains incomplete.

The main admin dashboard previously read `products.ingredients_raw`, which intentionally remains empty under the canonical-formulation design. Its ingredient count, product filters and forensic ingredient panel now read published canonical formulas through identity-verified retailer associations. Category filters use that verified canonical classification too. Raw retailer values remain unchanged and available for inspection; an unverified association cannot borrow the canonical's formula. The live API now shows 115 published skincare formulations and 316 covered retailer listings while all 22,155 original retailer ingredient fields remain empty. Fixed numerical UI fallbacks and unsupported ingredient-concentration labels were removed.

An overbroad publication blocker also confused an unaccepted retailer's fuzzy candidate list with a dispute about an existing canonical. Grounded source suggestions no longer invalidate unrelated accepted associations. Explicit canonical findings, disputed accepted source links, strong-identifier conflicts and ungrounded review cases remain blocking. This correction verified **126 more independently corroborated identities** and published **17 more saved retailer declarations** without model calls.

Product-type precedence was corrected: explicit retailer hair types beat generic `cream`, `serum` and `mask` vehicle words. A backed-up repair removed **243 hair products** from skincare classifications, corrected additional explicit roles and resolved **20 more category reviews**, preserving specific role disagreements and approved classifications.

New detail-page acquisition reads exact known retailer URLs, validates product headings and brands, follows bounded continuation offsets, and retains the literal page with provenance. It fills ingredient tabs omitted by the Shopify product feed. A declaration on a partial page requires the literal full ingredient text and an observed closing product-section heading; truncated ingredient tails do not qualify. Ambiguous “What's in it” summaries stay partial evidence, including long active-only summaries. Across the pilot and five batches this published **43 more declarations** and retained **37 partial lists**, with **zero model calls**. It does not change prices, stock, identity links or source-wide publication settings.

The public product cache now follows committed catalog revisions. Queue writes and no-op updates do not invalidate it. A live batch increased searchable products with ingredients **101 → 113 in the same API process**, with a 0.66-second refresh and no restart. The underlying legacy-offer lookup lacked an index: its scan exceeded a bounded two-second preflight; the indexed query completed in 0.0057 seconds. A full catalog reload measured 0.65 seconds.

Validation: **475 regression tests pass**, the frontend production build passes, SQLite integrity is `ok`, foreign-key violations are zero, and every prepared or completed identity pair contains exactly two product objects. See `catalog-admin-live-verification-2026-10-26.json`, `catalog-cache-live-verification-2026-10-26.json`, `catalog-health-latest-2026-10-26.json`, and the numbered source-detail reports for current evidence and backup paths.

## Matching

Every physical Gemini identity request compares exactly two product objects. Source products with several candidates generate separate pair requests; the application combines their verdicts locally. Existing canonical clusters also compare their individual listing pairs. The API rejects `batch_size: 10` with HTTP 422.

Completed pairs survive interruption. Unchanged two-product inputs are reused even when another candidate is added to the review. Changed inputs generate a new pair key. Reused answers are still checked against current application evidence.

The first queue job completed 319 comparisons, resolved 72 review cases and applied 264 negative candidate links before a request failure. A subsequent bounded run confirmed HTTP 429. Further provider calls were stopped; queued work and completed decisions remain saved. The failure UI now distinguishes a failed request from previously successful applications and reports a credential-free HTTP status.

The current pair table contains 327 completed verdicts and 4,073 prepared comparisons. Every saved payload contains exactly two products. Preparation is not counted as an API request or completed verdict.

## Categories and formulas

Three category repairs resolved 798, 72 and 265 reviews from retained source evidence: 2,246 pending category reviews became 1,111. Separate canonical projections corrected 171 and 49 categories. Human-approved classifications and contradictory retailer clusters were preserved.

Changes cover bounded opening product declarations, exact retailer product types, hair-loss products, masks, lip products, explicit hygiene merchandise and sun essences. Ingredient mentions and instructions to use another product are not opening product declarations. Nutrient names alone no longer establish an oral supplement.

Retained evidence independently verified 54 more canonical identities and published eight more complete ordered Egyptian retailer formulas. Publication remains individual; no source-wide publication policy was enabled. Approved dynamic retailer sources now count as completed INCI acquisition only when an authoritative publication audit matches the current formula and source.

The MCP research worker was incompatible with the installed SDK's snake_case fields. It now supports both field conventions. A live read successfully retrieved the retained Secalia retailer page. Search timeouts no longer discard known exact retailer URLs. The pilot did not publish new researched ingredients.

## Reviewed brand duplication

`ISISPHARMA` and `Isis Pharma` were separate legacy brands. The [manufacturer Secalia page](https://www.isispharma.com/en/product/face-cream/) and [Egyptian retailer listing](https://www.bloompharmacy.com/products/isis-pharma-secalia-face-cream-40ml) support their equivalence. The consolidation retains snapshots, source URLs, reviewer identity and reason in `brand_identity_history`; it does not itself merge or verify products.

Six exact marketed-product duplicate groups subsequently passed separate checks and were merged through the reversible canonical merge workflow. Formula-bearing records survived. Three other groups were preserved because of pending identity reviews or non-exact retailer listings. Product titles and source observations were retained.

## Earlier checkpoint and validation

At the earlier checkpoint, the restarted API reported 7,342 active canonicals, including 6,200 skincare products, 566 verified identities, 59 published ingredient lists, 973 pending identity reviews and 1,111 pending category reviews. Six redirected canonicals remain in historical storage; current totals exclude them. The verified count decreased by three when duplicate verified identities were merged.

SQLite integrity is `ok`, with zero foreign-key errors. The frontend production build and all 453 regression tests pass. A subsequent 56-test matching run covers the latest cached-pair reuse change; 83 category/repair checks cover the latest hygiene rules. Focused checks also cover audited brand consolidation, merge conflicts, MCP SDK compatibility and source-formula readiness. The recommendation ranking tests now use explicit fixtures instead of relying on mutable catalog titles and visibility.

The catalog is not complete. Most skincare products still lack published source-backed formulas. Additional identity comparisons are queued; provider HTTP 429 prevents continuing model requests at present. Ambiguous products and conflicting formulas have not been declared correct to reduce queue counts.

The accompanying JSON reports contain backup paths, decisions and counts. `catalog-health-2026-10-26.json` captures the current database audit. Each applied data repair has a pre-change SQLite backup under `api/backups/catalog/`.
