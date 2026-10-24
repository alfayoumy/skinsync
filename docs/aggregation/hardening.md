# Security, identity and INCI hardening requirements

**Current implementation/readiness policy — 25 October 2026.** This supersedes old one-commit hardening assessments. A green regression workflow validates code against a disposable catalog, not live source or production correctness.

## Authorization

`api/security/admin.py` uses `AdminAuthentication` and `SKINSYNC_ADMIN_CREDENTIALS` (JSON actor→token/permissions), with a minimum 32-character token and explicit scopes:

- `read`: admin reads, review lists, Gemini decisions, diagnostics.
- `collection`: source collection, multi-source discovery, offer-only refresh.
- `identity`: product identity auto-verification, review preparation, Gemini identity queue/replay, brand alias/constraints/merge/split/reversal, controlled rollout.
- `formula`: INCI waterfall, model/ingredient queues and formulation writes.
- `formula_correction` **plus** `formula`: explicit authoritative correction/force-approval APIs.
- `operations`: operations not assigned a more specific scope.

The middleware responds 503 when credentials are unconfigured, 401 for missing/invalid tokens and 403 for insufficient permissions. Do not embed real tokens in source, docs or frontend. Frontend development only forwards tokens to trusted localhost API origins by default.

## Source and identity controls

- Source adapters are restricted by registered source ID, HTTPS domain, market, enablement, robots policies where applicable, and pacing. `publish_enabled` is independent from `enabled`.
- Legacy offers remain traceable; immutable-ish raw observation snapshots preserve source evidence, and source-specific variants are not collapsed into arbitrary first-variant prices.
- Automated title matching does not automatically allow source-to-canonical linking. Require approved/curated brand aliases and compatible SPF, active strength, formulation vehicle, fragrance/tint, timing and revision attributes.
- No transitive fuzzy merge solely from token overlap; no automatic canonical merge based on Gemini.
- A confident Gemini `same_product` at ≥0.90 can update identity only after deterministic safeguards. A confident `different_product` may be split only into explicitly anchored variants with audited cannot-link constraints; otherwise retain a genuine structural review.
- Previously evaluated Gemini identity cases can be replayed locally without provider calls, with saved evidence hashes, audited history and a preview/apply boundary.

## Formulation/INCI trust

- Identity verification is not proof of INCI. Manufacturer/regional full list evidence takes precedence over unrelated market formulations or ingredient-reference candidates only when exact-product identity is supported.
- Partial lists/key actives, truncated ingredient text, search snippets and LLM approval are never sufficient alone for authoritative full INCI publication.
- Ordered ingredient hashes preserve sequence; a conflicting full formula must not overwrite an existing approved version without a reviewed correction.
- Use `FormulationRepository`, `formulation_versions` and `inci_audit_evidence` for publication/audit; do not write directly to canonical INCI from arbitrary retailer/LLM workflows.
- Explicit correction endpoints require stronger permissions.

## Governance and evaluation

- Label independently sampled source observations and mismatched identity pairs. Source evaluation results and reconciliation previews have SQLite immutability triggers; labels close after finalization.
- A release gate should inspect cross-category conflicts, duplicate identity candidates, changed source/formula evidence, unverified products, source coverage, review backlog, and test/CI evidence.
- The numerical JSON benchmarks checked into `docs/aggregation/` are snapshots; they must not be reported as fresh/live catalog precision metrics without rerunning evaluations.

## Deployment risks and follow-up

- Python 3.12 is used for CI, while current `api/Dockerfile` uses Python 3.9. Validate and align the container environment before release.
- The legacy `daily_sync.py` mixes scraping, product creation and price updates; the newer separated catalog workflows are recommended for controlled operational runs.
- Some HTML sources lack full-catalog adapters and require explicit product URLs.
- Back up SQLite consistently under WAL before bulk changes, verify `SKINSYNC_DB_PATH` and monitor task lifecycle and actual database updates.

[Operations and security guide](../workflows-and-operations.md) · [Database](../database-reference.md) · [API reference](../api-reference.md)
