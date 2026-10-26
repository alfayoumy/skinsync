# INCI remediation — current follow-up and archived October 24 baseline

Latest data and implementation checkpoint: [26 October evening recovery](aggregation/catalog-repair-evening-progress-2026-10-26.md).

**Current implementation review: 25 October 2026.** This document preserves the previous October 24 remediation assessment below, but many additional identity and operational changes have landed since it was written.

Current source-of-truth references: [architecture](system_architecture_and_api_documentation.md), [INCI operations and product workflows](workflows-and-operations.md), [API routes](api-reference.md), [database](database-reference.md), [aggregation runbooks](aggregation/README.md).

**Important separation:** canonical identity verification, approval of a region-specific full ingredient list, and product/price ingestion are independent. Gemini identity matching now uses its own persistent review queue; saved ≥0.90 confidence verdicts may be replayed **without new model calls**, but only after exact identity/variant checks. A confident different-product verdict may produce an audited provisional split only if the store listings permit a deterministic partition.

The historical "complete" wording below describes the **code changes under that review**, not proof that the real production catalog has undergone an end-to-end INCI publication run, that external source terms are permitted, or that all formulas are current. Validate completeness, regional variation and audit evidence on the deployed database before a broad write.

**Original October 24 status snapshot follows.**

---

## Historical: INCI remediation implementation — 2026-10-24

The corrective implementation for the review of `463ebee` is complete. Full-catalog publication and external audit export remain separate operational steps; neither was performed.

The working tree initially contained unfinished corrections across 16 files. Those edits were preserved and completed. The pre-existing modified `api/skincare.db` is excluded from the implementation commits.

## Acceptance criteria

| Review requirement | Implemented behavior and verification |
| --- | --- |
| Canonical bulk publication | Resolve unique canonical IDs and publish through FormulationRepository; sequential and threaded tests verify canonical rows and audit evidence without offer formulation writes. Worker connections use the supplied database. |
| Protect accepted formulas | Conflicting ordered formulas preserve the existing version and record review evidence. An explicit admin correction can replace a formula, but cannot bypass candidate validation. |
| Jev row handling | Queue records become dictionaries. An approved result using real SQLite rows publishes exactly once without changing offer ingredients. |
| Publication boundary | Named legacy routes, jobs, workers and manual tier application publish through the repository. Harvesting retains HTML evidence; new offer formulation fields remain empty. Pending matches remain unverified evidence. Existing offer ingredients cannot suppress canonical publication. |
| Complete and validated evidence | Partial, unverified, truncated, masked, malformed and explicitly unresolved candidates cannot publish. Truncation survives sanitization and extraction limits. Whitelist results remain partial evidence. |
| Retire Tier 6 | The resolver returns no propagation result; manual comparison cannot execute it and manual application rejects it. Failed tiers never clear accepted formulations. |
| Explicit ID namespaces | Similarity requests require canonical/product namespaces, including non-colliding IDs. Collision regression tests prevent silent entity substitution. |
| Identity and extraction | Source matching includes deterministic variant gates; OpenBeauty uses the full cleaned title as its cache key. Canonicals start without inherited ingredients. Existing canonical IDs survive retitling. Ambiguous links and conflicting formulations create review cases rather than automatic merges. |
| Transactions and history | Publication and evidence use a savepoint. Audit insertion failure rolls back publication while preserving unrelated caller changes. Caller-owned transactions are not committed by the repository. Corroboration preserves the formula version. |

## Canonical identity review

The linker distinguishes finding an existing canonical, proposing a new canonical, detecting duplicate existing canonicals, and detecting incompatible/mixed clusters. Pairwise identity checks also prevent generic intermediary titles or shared reference IDs from bridging incompatible variants. Multiple existing anchors are preserved; ambiguous new offers remain unlinked. Pricing is aggregated over all linked offers, including protected clusters.

Review cases are durable and idempotent. Repeated identical evidence preserves a review decision; changed evidence reopens it. Review confirmation/dismissal records a reviewer and notes and never silently merges, splits, deletes or publishes product records.

Admin endpoints:

- `GET /admin/identity/audit`: read-only catalog assessment.
- `POST /admin/identity/audit`: persist review cases.
- `GET /admin/identity/reviews?status=pending`: inspect review evidence.
- `POST /admin/identity/reviews/{id}`: record `confirmed` or `dismissed`, with `reviewer` and `notes`.

Read-only command:

```bash
python3 api/scripts/audit_canonical_identity.py --output /tmp/identity-audit.json
```

The actual catalog audit inspected **14,710 canonicals and 22,800 linked offers** with **zero database writes**. It found **603 review findings**: 9 possible duplicate canonical pairs, 403 mixed-cluster findings, and 191 formulation-evidence conflicts. Categories overlap; these are review findings, not 603 proven bad products. Their evidence must be assessed before approving catalog corrections. The implementation intentionally preserves all existing identities and formulas.

## Testing

The complete isolated regression suite passed **166 tests**, including HTTP integration tests. External providers, model downloads and store refreshes are stubbed; application HTTP routes, SQLite publication and audit transactions execute normally against a disposable snapshot.

```bash
python3 api/tests/run_regressions.py
```

`SKINSYNC_DB_PATH` selects the source catalog. The runner snapshots it to a temporary database and directs all application database access there. It does not run against the source database. Cross-encoder reranking is disabled for deterministic offline verification.

The earlier TestClient hang was isolated to the execution sandbox: a trivial standalone FastAPI route passes outside that sandbox. The complete suite was therefore run outside the sandbox, with database isolation and external API calls disabled. HTTP tests are no longer an outstanding verification gap.

An additional **39 publication/comparator tests pass** after the final masked-input, bracket-validation and single-product publication-status guards. `compileall` and `git diff --check` also pass.

An earlier local-source, read-only waterfall pilot inspected 10 real canonical products: 1 publishable/corroborating result, 9 unresolved, zero writes. Three additional full store candidates were excluded by conflict protection. Live provider availability and model quality are not established by offline regression tests.

## Deployment migration

Apply the additive database migration before starting updated workers or the API:

```bash
PYTHONPATH=api python3 -c 'from pipeline.db_manager import init_db; init_db(seed_defaults=False)'
```

This adds the unordered diagnostic fingerprint column without changing accepted formulas. Identity review storage is created lazily by the linker or the audit persistence endpoint. The regression runner applies migrations to its disposable snapshot automatically.

## Operational follow-up

Review the catalog findings and authorize any data corrections separately. A complete waterfall publication run and external audit export remain deferred as specified in the supplied review. These are operational/data decisions, not unfinished implementation or excluded HTTP tests.
