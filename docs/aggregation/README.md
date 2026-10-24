# Product identity and multi-source aggregation

Implemented on top of the canonical-publication remediation. This is an additive architecture migration, not a production reclustering or a full waterfall run.

## What changed

| Area | Implementation |
| --- | --- |
| Baseline | Read-only catalog profiler with record-level category, eligibility, orphan, evidence, size, category-disagreement, barcode and identity anomalies. |
| Taxonomy | Structured classification with original evidence, function, area, vehicle, usage, features, concerns, confidence, source and review status. Compatibility category labels remain available through `classify_and_tag()`. Fragrance-free creams, tinted sunscreens, baby creams and cleanser/exfoliator overlaps have regression coverage. |
| Brands | Persistent brands and explicit approved aliases. Unknown brands go to review. Exact legacy labels are preserved separately; fuzzy brand strings never approve identity. |
| Resolution | `CanonicalIdentityResolver` retrieves by durable mapping, validated GTIN, manufacturer ID, brand profile and normalized aliases. Similarity retrieves candidates; it does not approve a merge. Missing critical attributes mean unknown. Contradictory attributes block the association. Identical INCI and shared reference IDs are not identity proof. |
| Stable IDs | Incremental linker replaces union-find. Existing IDs, canonical names and formulas remain stable. Old normalized keys remain preserved. Explicit merge/split operations have before/after history, redirects and guarded reversal. Cannot-link decisions persist. |
| Observations | Immutable, content-addressed source snapshots retain original title, brand, category, payload, URL, timestamp, market, language, identifiers, variants and ingredient-section context. Identical repeat acquisition reuses the snapshot; changed content creates a new version. |
| Sources | Registry with source type, domain, platform adapter, market, language, evidence reliability, section rules, collection delay, collection status and separate publication gate. 31 source entries cover existing stores and staged onboarding waves. Registration is not a quality certification. |
| Adapters | Shopify, WooCommerce with expanded variations, product-page/JSON-LD, and Open Beauty Facts contracts. Existing Shopify synchronization retains its compatibility rows while capturing every variant in `product_offers`. Non-Shopify sources dispatch through their registered adapter. |
| Offers | Composite source/product/variant uniqueness allows the same URL to have several prices, SKUs and stock states. Multipacks carry count, unit size and total quantity. Mass and volume remain separate. Public price queries suppress duplicate legacy page representatives and exclude unapproved expansion-source offers. |
| Ingredients | Identity is resolved first. Declarations become evidence and a resolution-queue item. Exact source evidence enters the controlled waterfall. Regional compatibility, completeness and order are required. Conflicts go to review; authority chooses a representative only after declarations agree. No generated ingredients are accepted. |
| History | Approved formulation versions are retained separately. Retail ingestion cannot replace canonical INCI. Explicit evidence corrections still pass repository validation. |
| Review | Unified identity, duplicate, category and formulation queue, with remembered decisions. The hamburger menu’s **Source Registry** entry opens all registered sources, including staged sources with no products, source-specific setup and review coverage. The main dashboard keeps the catalog explorer and its dynamic retailer filter. Reviewed aliases, categories, links and mismatches are durable. |
| Rollout | Collection and publication are separate switches. New-source canonical products are provisional and hidden. Activation requires a named reviewer, a labeled evaluation report and passing quality gates. No source was activated by this implementation. |

Classification confidence is a deterministic rule score, not a calibrated probability. The 30-item engineering gold set is deliberately difficult but small; its score is not a population accuracy claim. International manufacturer declarations remain ineligible for automatic publication until regional compatibility is explicitly reviewed. Bundles and unresolved categories remain staged; they do not receive a shared canonical INCI.

## Safe migration

Run from the repository root, with the API/workers stopped for an actual deployment migration. The default command snapshots the real database but applies the migration only to a disposable copy:

```bash
python3 api/scripts/migrate_aggregation.py --backup /tmp/skinsync-before-aggregation.db
```

For an approved deployment, choose a new backup path and add `--apply`:

```bash
python3 api/scripts/migrate_aggregation.py --backup /path/to/new-backup.db --apply
```

`--database` or `SKINSYNC_DB_PATH` selects another catalog. Existing tables are not rebuilt. `products.product_url` remains unique for page-level compatibility; the new `product_offers` table provides variant-level uniqueness. Every existing product/canonical ID mapping is captured and checked. New tables include brands, aliases, taxonomy, sources, snapshots, variants, offers, links, constraints, redirects/history, formulation versions, review cases and release approvals.

The migration was dry-run against the actual catalog: **22,800 of 22,800 mappings preserved**. The additive migration was applied to `api/skincare.db` on 24 October 2026. The pre-migration backup and verification report are retained in `data/backups/` outside the repository. Existing product rows, canonical data and audit evidence were verified against that snapshot; integrity is `ok`, with no new foreign-key violations. Source publication remains disabled.

## Diagnostics and reconciliation

```bash
python3 api/scripts/profile_aggregation.py --output /tmp/catalog-baseline.json
```

Baseline over the real catalog:

- 22,800 retailer records; 14,710 canonicals; six stores; zero unlinked records or orphan canonicals.
- 1,606 proposed category changes and 737 potentially excluded skincare records.
- 2,542 formula-bearing canonicals without an `approved_authoritative` evidence record.

These are review candidates, not proven errors. Reports retain source URLs and record IDs.

`POST /admin/aggregation/reconcile` builds a disposable SQLite copy, runs the incremental resolver there, and returns proposed associations and review cases. The full-catalog preview preserved all **14,710 existing canonical identities**, created **zero** new canonicals, changed **zero** product/canonical associations and made **zero** live writes. It produced 1,347 pending review cases. Existing associations are intentionally reviewed rather than silently reassigned.

A pending caller transaction must be committed or rolled back before requesting a reconciliation snapshot.

## Source onboarding and coverage

`GET /admin/aggregation/sources` lists registry entries. Existing six-store collection remains operational; expansion sources remain disabled by default. `PATCH /admin/aggregation/sources/{id}` changes collection status only. It cannot approve publication.

- `POST .../sources/{id}/ingest`: normalize supplied source payloads; defaults to `dry_run: true`.
- `POST .../sources/{id}/collect`: bounded explicit URLs, registered domains, robots policy and collection delays. HTML-backed product detail enrichment also works for Shopify sources. Unknown/disallowed access is deferred.
- `GET .../sources/{id}/coverage`: distinct discovered products, canonical matches, offers, candidate lists, full ordered declarations, pending review and publications.
- `POST .../sources/{id}/rollout`: reviewed source-specific quality report. Enforces category macro-F1 ≥ .95, skincare false exclusion ≤ .01, automatic-link precision ≥ .995, exact offer/durability/preservation results, and zero critical merges, wrong-product formulas or unintended new entities. These are release gates, not measured production claims.

For a read-only live benchmark on explicit product pages:

```bash
python3 api/scripts/benchmark_sources.py \
  --manifest docs/aggregation/source-smoke-manifest.json \
  --output /tmp/source-coverage.json
```

This snapshots the catalog, checks access rules and measures extraction and identity outcomes without enabling sources or publishing. Expected canonical IDs can be supplied as independently reviewed labels. An unlabeled smoke run does not establish automatic-link precision or catalog-wide coverage.

Live smoke pages were checked for Source Beauty, Feel22, ZYNAH, Zahra Egypt, CeraVe Egypt and Jamila Egypt. Extraction handles dedicated tabs/accordions, full versus key-active sections, bullets, English/Arabic headings, chemical slashes, ppm commas and formula codes. Related-product JSON-LD must match the requested page before its identity can be used. Jamila's Arabic full-list heading was verified against captured live HTML, added to regression tests and rechecked live successfully. All six smoke pages produced full-declaration candidates; none were published. See `validation-results.json` for the measured sample results.

Source examples and domain verification:

- [Source Beauty product](https://sourcebeauty.com/products/eveline-serum-shot-multi-peptides-rejuvenation-serum)
- [Feel22 product](https://eg.feel22.com/products/skin1004-hyalu-cica-brightening-toner)
- [ZYNAH product](https://zynah.me/products/dermacy-pore-cleansing-face-gel-200ml)
- [Zahra product](https://zahraegypt.com/en/shop/product/2954/810400030824)
- [CeraVe Egypt product](https://www.cerave.eg/en/skincare/cleansers/hydrating-facial-cleanser)
- [Jamila product](https://jamila-eg.net/collections/acne/products/dr-althea-345-relief-cream)

## Review and controlled publication

All routes below use the `/admin/aggregation` prefix:

| Route | Purpose |
| --- | --- |
| `GET /observations`, `GET /reviews` | Paginated source records and review evidence. |
| `POST /reviews/{id}` | Reviewed link, cannot-link, category, keep-separate, dismissal or evidence request. |
| `POST /brands/aliases` | Explicit brand-name equivalence; conflicting alias reassignment is rejected. |
| `POST /identity/constraints` | Record a cannot-link relationship between canonicals. |
| `POST /identity/merge` | Reviewed duplicate merge; contradictory brands, attributes or accepted formulas block it. |
| `POST /identity/{id}/split` | Move selected legacy/variant offers to a new provisional identity, without copying INCI. |
| `GET /identity/history`, `POST /identity/history/{id}/reverse` | Audited history and reversal; intervening changes block automatic reversal. |
| `POST /identity/{id}/publish` | Explicit approval of a resolved, classified provisional identity. |
| `GET /formulations/{id}` | Assess source evidence without publishing. |
| `POST /formulations/{id}/resolve` | Publish concordant eligible evidence only after source release approval. |
| `POST /formulations/evidence/{id}/approve` | Named, reasoned approval/correction of a selected declaration; foreign-market compatibility requires explicit confirmation. |

Re-ingest an observation after accepting its identity or category review. Decisions are tied to evidence; changed evidence reopens review. A repurposed source page cannot inherit a former product's association just because its source ID is unchanged. Legacy prices are preserved when the new page identity is not accepted.

## Verification

```bash
python3 api/tests/run_regressions.py
npm --prefix skinsync-app run build
```

The regression runner uses a disposable database copy and mocks external providers and model downloads; HTTP routes execute normally. It must run outside restricted loopback environments where FastAPI TestClient cannot operate. The full suite passed **222 tests**, including HTTP integration tests. The new panel also passes its ESLint check. Additional merge/reversal guards were verified in the focused aggregation suite.

The checked-in taxonomy benchmark and source manifest are reproducible validation artifacts. No production category mass update, canonical merge/split, source activation, full waterfall or external catalog export was performed. The previously modified `api/skincare.db` is retained and excluded from implementation artifacts.

Live diagnostics are available in the hamburger Source Registry (per source, all sources,
and a selected product URL) and the 3-API Status & Quotas dialog (per provider and all).
Reachability and functional results are separate. Source tests parse one bounded sample,
check expected prices and ordered ingredient evidence, honor robots and collector pacing,
and never ingest or publish products. An incomplete sample does not establish catalog-wide
failure; HTML sources without a sample explicitly require a product URL. API tests send one
small request using the production protocol and validate response content/schema. HTTP
401/403/429 means reachable with an unsuccessful functional check. Results and running
state are process-local, shared across dialogs, and reset on backend restart. At most three
tests run concurrently; repeated clicks do not duplicate an already queued/running target.
