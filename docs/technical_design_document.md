# SkinSync technical design — current implementation

**Code-based revision:** 25 October 2026. This is the current implementation map; it supersedes old 4-gate-only architecture summaries for product identity aggregation. Full contracts: [HTTP API reference](api-reference.md); [database reference](database-reference.md); [operational workflows](workflows-and-operations.md).

## 1. Domain and identity rules

**Source observation** retains source-specific identifiers, raw brand/title/category/description, packaging variants, ingredient section, region/market, date and payload hash. **Canonical product** is a durable product/formulation identity. **Retailer offer** is a saleable source+product+variant listing with price/stock/pack quantities. An INCI evidence row is not necessarily an authoritative published formulation.

One canonical can have many source observations/offers. Two canonical records must never be merged simply because names share tokens or an LLM rated them similar. Marketing translation and pack-size differences can be compatible; SPF, fragrance/shade, concentration, timing, vehicle, product line or formula revision conflicts block blind merging.

## 2. Source discovery and aggregation

The source registry (`sources`) categorizes `retailer`, `manufacturer` and `reference` sources with adapter type, market, access flags, rate limits and publication policy. Adapter modules parse Shopify products/variants, HTML product pages and supported reference formats into normalized observations. `aggregation_service.process_source_observation` persists observation snapshots, classifies and constructs an identity candidate, resolves against canonicals and writes initial offers where appropriate.

Multi-source collection is tracked in `pipeline_tasks`: Shopify `pages=0` means scan to an empty page, with rate limits, strict network-error distinction, repeated-page detection, and progress per source/page. Unsupported whole-site traversal is never mislabeled complete. Third-party HTML collection uses HTTPS/domain checks, robots policy and bounded URL batches.

The original `harvester.py`/`daily_sync.py` remain compatibility paths but should not be substituted for strict workflow separation.

## 3. Classification and uniqueness

`taxonomy_service` classifies skincare/beauty vertical, primary category and identity attributes independently of retailer merchandising labels. `brand_registry` normalizes curated aliases and supports audited approved aliases. `canonical_identity_resolver` uses source IDs, exact/near title normalization, brand family and structured identity gates; cannot-link decisions, aliases and redirects protect known splits/merges.

Legacy identity review is bounded; a preview evaluates all remaining legacy canonicals, while Apply commits at most `max_verified` safe records and persists genuinely conflicting cases. A second Apply does not repeat already-verified identities. Unsafe cases remain in `product_review_queue` and retain retailer evidence.

## 4. Gemini product identity worker and saved-decision replay

`product_identity_gemini.py` sends structured case batches to Gemini with one response per review ID. It shares the configured Gemini limiter with the INCI provider path but uses a **separate identity review queue** (`identity_gemini_queue`). The stored `verdict`, `confidence`, `explanation`, `status` and evidence hash permit replay without another model call.

- Same-product verdict ≥0.90 + independent exact-source/brand/variant/INCI/duplicate checks → verified canonical links, review closed and audit history.
- Different-product ≥0.90 → **not** same-product approval. `saved_gemini_decisions.py` can attempt a deterministic split for explicit variant-marker groups with an anchored retained canonical, creating provisional new canonical IDs and cannot-link constraints; unpartitionable cases remain for review.
- Uncertain, absent, malformed, stale, contradictory or ineligible → no automatic identity write.

Replay endpoints support dry-run projection, tracked application and stop. The sortable/paginated `GET /admin/aggregation/reviews/gemini/results` returns completed and pending model decisions with separate model and application explanations, full listing evidence and evidence-current flag.

## 5. Existing offer refresh

`update_existing_shopify_offers` only updates previously linked `product_offers` rows by registered source product+variant ID, after checking brand registry, marketed identity, SKU/GTIN and hard variant attributes. It updates `price`, `compare_at_price`, availability, unit price and canonical offer aggregates. Unknown variants/mismatches are counted and skipped. This does not write new canonicals, classifications or formulas. Currently finite and synchronous.

## 6. INCI publication and forensic evidence

`waterfall_readiness.eligible_waterfall` requires `identity_status='verified'` and no pending identity conflict. Only existing canonical skincare IDs are eligible; no retailer catalog discovery happens here.

`waterfall_inci_resolver` retrieves available direct/offline/provider candidates, differentiates incomplete/partial listings and candidate evidence, and uses `FormulationRepository` for controlled canonical formulation publication. Ordered tokenization and hashes preserve ingredient ordering; unordered hashes help detect related compositions but do not prove identical formulations. Formulation region, source product identity, source completeness and already published content govern approval versus conflict.

INCI model queues (Jev and Gemini) are independently started via `/admin/queues/*`, unlike the Gemini identity queue under `/admin/aggregation/reviews/gemini/*`.

## 7. Price and recommendation services

`price_service` compares retail offers for equivalent packages/sizes and supports deal views. `catalog_service` loads canonical products for consumer catalog queries; `recommender` and `similarity` implement ingredient/title scoring, clinical skin profile considerations and optional reranking. The system contains legacy hybrid matching code paths; consult those modules for exact live scoring gates rather than assuming old benchmark equations are unchanged.

## 8. Governance and audit

Product identity decisions are persisted in `product_review_queue`, source-to-canonical links in `product_identity_links`, and audited actions in `canonical_identity_history`. Manual merge, split and reversal are explicit endpoints with reviewer+reason. Model confidence does not grant authority to override declared cannot-link constraints. Approved INCI is versioned; source evidence is retained.

Source evaluation labeling/finalization and reconciliation previews have immutable SQL triggers. Status changes and task logs are stored in `pipeline_tasks`/`pipeline_task_logs`. Enforce scoped admin tokens and create a WAL-consistent SQLite backup before bulk changes.

## 9. Verification and operational limits

The GitHub workflow tests a disposable database with mocked network calls; never present those results as measurements of your live catalog or proof of provider availability. Python 3.12 and Node 22 are the tested development/CI runtimes. The `api/Dockerfile` currently uses Python 3.9, which should be updated and validated separately before container release.

See [operations](workflows-and-operations.md) for task lifecycle, configuration, runbooks and release gates, and [database](database-reference.md) for every currently declared table/column.
