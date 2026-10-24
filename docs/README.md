# SkinSync documentation

**Current documentation index — 25 October 2026.** The guides below describe code in the SkinSync repository, not live production measurements or an executed catalog migration.

## Start here

| Guide | Purpose |
|---|---|
| [Architecture and API overview](system_architecture_and_api_documentation.md) | Current system architecture, boundaries and reference links |
| [Complete HTTP API reference](api-reference.md) | **All implemented route decorators**, authorization, core request examples, and source-code links |
| [SQLite database reference](database-reference.md) | Full declared table/column inventory, key relationships, additive migrations and read-only diagnostics |
| [Workflows and operations runbook](workflows-and-operations.md) | Ingestion, offer refresh, identity, Gemini, saved replay, INCI, security, deployment and recovery |
| [Technical design](technical_design_document.md) | Detailed identity, taxonomy, offers, formulation trust and background job design |
| [Aggregation/index](aggregation/README.md) | Source registry, hardening, evaluations, review and reconciliation |
| [Catalog operations](aggregation/catalog-operations.md) | UI-level operator instructions for the four independent flows |
| [Gemini identity review](aggregation/gemini-product-identity-review.md) | Batch Gemini decisions and manual fallback |
| [Apply saved Gemini decisions](aggregation/saved-gemini-decisions.md) | Reuse already-paid-for model outputs without another provider call |
| [Refresh approval and recovery](aggregation/refresh-runbook.md) | Readiness, careful rollout and verification |
| [Hardening/release requirements](aggregation/hardening.md) | Permission boundaries and product/INCI validation controls |
| [INCI remediation status](inci-remediation-status.md) | Historical implementation status and current safeguards |

## Historical analysis (not operational sources of truth)

- [SOTA architecture evaluation](sota_architecture_evaluation.md): research and performance analysis as of its original date, not a description of all current identity/INCI workflows.
- [Enhancement plan](enhancement_plan.md): original engineering plan, with a current-status preface; older milestone figures are historical, not live catalog stats.
- The aggregation subfolder contains JSON/CSV evaluation **artifacts**; they are recorded evidence snapshots, not configuration instructions or continuously updated dashboards.

## Accuracy and ownership

The definitive runtime contracts are `api/app.py`, `api/routers/*.py`, `api/security/admin.py` and Pydantic/OpenAPI at `/openapi.json`. SQLite schema definitions are in `api/pipeline/db_manager.py`, `api/pipeline/aggregation_schema.py` and `api/pipeline/identity_review.py`. The CI regression suite is `api/tests/run_regressions.py`. Update these documents when you add API routes, schema columns or job state transitions.

All links in this index are **repository-relative** so they work in GitHub and local Markdown preview. No Windows-local paths, database contents, tokens or unpublished screenshots should be committed to documentation.
