# Product identity and multi-source aggregation

**Current implementation guide — 25 October 2026.** This replaces the older patch-status narrative. For every endpoint see [HTTP API reference](../api-reference.md); for all tables see [database reference](../database-reference.md); for end-to-end operations see [workflows runbook](../workflows-and-operations.md).

## Scope

Registered sources fall into **retailer**, **manufacturer** and **reference** categories. Registry entries are not automatically collected, and an enabled source is not automatically approved for authoritative ingredient publication.

- `source_registry.py` defines source inventory and policy (domain, adapter, region, access flags, rates, evidence confidence).
- `source_collection.py` validates registered URLs and robots policy for the bounded URL collector.
- Shopify product discovery supports tracked multi-source **All pages** mode (`pages=0`); HTML/other sources need provided or previously recorded product URLs, and missing coverage is explicitly reported as `needs_urls`.
- `aggregation_service.process_source_observation` records source observations, variants, classification and identity candidates and creates initial offers where applicable.
- `brand_registry.py` / `taxonomy_service.py` / `canonical_identity_resolver.py` provide brand aliases, taxonomy, identity attributes, approved source links, explicit cannot-link constraints and provisional identity handling.

## Separate operational tracks

| Track | UI | Primary backend |
|---|---|---|
| New source discovery and identity | Catalog Operations → Add & match products | `/admin/workflows/products/discover-multiple` and `/admin/aggregation/sources/{source_id}/collect` |
| Existing retailer price/stock | Catalog Operations → Update prices & stock | `/admin/workflows/offers/refresh` |
| Full INCI evidence/publication | Catalog Operations → Find ingredients | `/admin/waterfall/batch/*` and `/admin/aggregation/formulations/*` |
| INCI model queue | Catalog Operations → AI review queues | `/admin/queues/*`, separate Jev/Gemini |
| Product identity Gemini queue | Product Matching Reviews | `/admin/aggregation/reviews/gemini/*` |
| Apply already stored identity Gemini verdicts | Product Matching Reviews → Apply saved decisions | `/admin/aggregation/reviews/gemini/saved/*` (no model calls) |
| Browse decisions and confidence | Product Matching Reviews → Gemini Decisions | `GET /admin/aggregation/reviews/gemini/results` |
| Manage source policies | Manage Product Sources | `/admin/aggregation/sources*` |
| Manually resolve genuine ambiguity | Product Matching Reviews | `/admin/aggregation/reviews/{review_id}` and audited identity operations |

**Important:** source discovery can create provisional identities and initial offers; existing offer-only refresh never imports unknown variants. Source collection, price updates, INCI waterfall and model worker calls do **not** imply one another.

## Identity integrity

Canonical IDs are durable, and distinct from `products.id` (legacy source row) or `product_offers.id` (saleable variant). A normalized title or identical ingredient-reference hit alone cannot justify a merge. Rules test marketed product line, actual brand registry, SPF, percentages, fragrance, tint/shade, vehicle and known formula differences.

Legacy exact identity check (`/admin/workflows/products/verify-existing`) previews all eligible historical canonicals and applies bounded batches; cases it cannot safely verify can be persisted through `/admin/workflows/products/prepare-reviews`. Human review is reserved for unresolved structural conflicts rather than every accent/marketing naming difference.

The identity Gemini worker sends multiple cases per provider call and stores verdict, score and reason in `identity_gemini_queue`; model confidence **≥0.90** is only a gate, not proof. Guarded same-product outcomes can verify identities, and explicit variant partitions can be split by replaying saved verdicts. Ambiguous cases stay for manual review. The [Gemini review guide](gemini-product-identity-review.md) and [saved-decision replay](saved-gemini-decisions.md) detail the rules.

## INCI provenance

`inci_audit_evidence` stores source candidates/approvals/rejections. Controlled publisher/versioning in `FormulationRepository` owns authoritative canonical INCI; source identity, region, ingredient-list completeness/order and conflicts matter. An LLM verdict about product identity never auto-approves INCI. See [INCI status](../inci-remediation-status.md) and [refresh readiness](refresh-runbook.md).

## Governed rollout and evidence

Source evaluations, labels and finalized reports persist separately with immutable SQL triggers. `/admin/aggregation/sources/{source_id}/evaluations`, `/admin/aggregation/evaluations/{run_id}/labels` and `.../finalize` support independent labeled review. Reconciliation previews persist a baseline hash; published rollout remains a separate approval step.

The JSON/CSV files in this directory (`catalog-review-manifest.json`, `taxonomy-benchmark.json`, `source-smoke-manifest.json`, `validation-results.json` and `review-samples/*`) are historical validation artifacts, not continuously updated production telemetry. Recompute benchmarks and refresh evidence before asserting current precision, coverage or accuracy.

## Operational links

- [Catalog operations](catalog-operations.md)
- [Gemini identity review](gemini-product-identity-review.md)
- [Stored decisions](saved-gemini-decisions.md)
- [Hardening requirements](hardening.md)
- [Refresh runbook](refresh-runbook.md)
- [Complete API](../api-reference.md)
- [Complete database](../database-reference.md)
