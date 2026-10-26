# SkinSync Admin V2 — next deployment checklist

**PR:** #18 (`feature/admin-v2-operations-wizard`)
**Status:** Draft / not merged / not deployed. Use an isolated SQLite catalog snapshot before touching a live catalog.

## Release scope already implemented on PR #18

- Authenticated Admin V2 main navigation and Operations Home, seven-step catalog wizard, independent repair and price/stock journeys.
- Persisted wizard drafts, step results, job association and operator events; real endpoint-driven discovery, saved-observation rematching, INCI waterfall, catalog quality audits.
- Central Job Center showing persisted tasks, INCI research runs and explicitly labeled process-local LLM/waterfall workers with only supported controls.
- AI/API Operations for Jev, Gemini, LangSearch and DonSeTch readiness; identity and INCI queues, bounded batches, preserved saved verdict replay, quotas, effective read-only settings, persisted batch presets.
- Page-checkpointed **existing-offer-only** refresh with cooperative pause/stop and explicit resume after crash/expired lease. Retains source identity and SKU/GTIN gates.
- **Centralized Match Arena** for easy manual identity and source-backed INCI decisions: one source-grounded pair with two real product cards, independent links/images (no invented missing imagery), visible variant, GTIN, SPF/identity metadata, ordered INCI, regional market, completeness, model and source evidence. Only **Match / No Match / Skip** decisions; optional comment; reviewer comes exclusively from logged-in admin context. A personal skip never confirms a case. All decisions use an evidence fingerprint under a write transaction and existing guarded domain mutation APIs. Session points/streak are non-authoritative visual feedback.
- Complex ambiguous canonical merges, multiple candidate canonicals, missing comparison links, conflicting identity evidence, and out-of-market INCI approvals are **not** forced into a simplistic one-click decision. Use Advanced Review Exceptions until a dedicated pairwise specialist flow is designed.

## Next deployment: pre-merge tasks

- [ ] Verify newest PR #18 CI **backend regressions**, **Vite production build**, and **ESLint** are green (do not rely on an earlier green commit).
- [ ] Inspect new Match Arena source links/photos on a statistically useful sample across each retailer; no broken, malicious or incorrect source-image pairing.
- [ ] Test all three actions on both queues with at least two authenticated reviewers. Confirm recorded user is derived from sign-in and client-supplied reviewer is ignored. Test concurrent votes and stale fingerprints (409, zero unintended write).
- [ ] Verify source-to-canonical Match and No Match do not auto-merge canonicals, erase offers, or publish INCI; confirm correct decisions and audit row are persisted atomically.
- [ ] Verify INCI Match publishes only with exact identity, complete ordered source declaration, registered evidence source and allowed market; otherwise returns 409 and leaves candidate pending. INCI No Match rejects only the candidate.
- [ ] Review which legacy `product_review_queue` and `inci_research_queue` cases are ineligible for quick-pair mode and audit counts. Multi-candidate conflicts and canonical-to-canonical merges require richer advanced tools rather than hidden/approving them.
- [ ] Validate pagination beyond the current first **500 candidate cases** and create a scalable server-side cursor, distinct eligible/blocked/skipped totals across the full backlog, and multi-reviewer work claiming if necessary.
- [ ] Test long queues, one page refreshed mid-decision, and successive fast clicks with no duplicate votes.
- [ ] Review keyboard/accessibility/mobile behavior: focus states, text wrapping, image fallback, explicit sources, source label and market warnings. Session game stats must not incentivize rubber-stamping; reward reviewed evidence, not Match specifically.
- [ ] Ensure database schema migrations are part of the approved initialization process rather than only request-time `CREATE TABLE`. Back up and verify WAL integrity.
- [ ] Run a clean sandbox end-to-end build and test real worker stop/resume/recovery on a catalog copy; measure SQLite locks and worker restarts.

## Next deployment: operating sequence

1. Take a **consistent SQLite backup** of the live catalog and validate `PRAGMA quick_check`, foreign keys, latest reviewer/INCI counters, source listings, canonical IDs and offer counts. Record the SHA-256 backup fingerprint and restore path.
2. Deploy backend/database migrations first into staging. Confirm authenticated read-only overview, both queue API GETs, filters, image URLs, reviewer context and permission boundaries.
3. Smoke-test only **read-only** Review Workbench comparisons. Inspect 20–30 real source-backed pairs and pending INCI research candidates against the underlying retailers. Verify link, variant, region and source INCI provenance.
4. Test bounded live actions on a disposable staging catalog, including skip, no-match, valid match, contradictory brand/SPF, out-of-market formulation, stale fingerprint, two reviewers acting on the same case and restart after checkpoint.
5. Deploy React V2 frontend after backend validation. Keep Advanced Review Exceptions and legacy guarded catalog/maintenance tools accessible during parity assessment.
6. With approved QA and backup, release live admin behind an operator-controlled rollout. Monitor error rates, recorded actor attribution, queue changes, skipped cases, mismatched links, INCI publications and active worker collisions.
7. Have a **rollback path**: restore previous frontend/backend version. Database schema additions are additive; if data was mutated by real decisions, code rollback does *not* undo those decisions. Restore a full database snapshot only with explicit owner approval and documented loss of any intervening updates.

## Backlog after this deployment (architecture RFC #17)

- Fully durable/leased/pauseable identity Gemini, Jev/Gemini INCI queues, waterfall and discovery workers. They still have process-local or cooperative-stop limitations.
- True workflow-wide atomic preview+apply fingerprints, revisioned policy approval, and fully idempotent job/worker restarts across all steps (current V2 step records are operational metadata).
- Native fine-grained classification and INCI publication gate, publication manifest and per-item exception handling in V2.
- Provider credentials/models/rate limits editing with permissioned revisions (currently read-only effective settings; local LangSearch quota counters not provider allowance).
- Pairwise multi-candidate evidence review, canonical-to-canonical merge/split and full formulation-difference comparison beyond the quick three-action queue.
- More robust review claiming, audit analytics, throughput metrics and persistent per-actor progress history.
- Remove legacy admin mega-components and dialogs only after all workflows and validation tests have feature parity.

**Operator warning:** merging PR #18 or passing CI does not by itself mean the system is safe to deploy. Do not run irreversible catalog mutations without a verified snapshot and test evidence.
