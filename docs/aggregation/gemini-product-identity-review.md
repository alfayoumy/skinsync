# Gemini-assisted product identity review

**Implementation reference — 25 October 2026.** Product identity Gemini reviews are **not** INCI formulation matching. They have an independent persistent queue and separate admin actions.

## What it does

The identity matcher flags unresolved product identities. Gemini receives **exactly two products per API request** and returns a verdict for that pair. A source with multiple candidates generates separate requests. Existing canonical clusters are decomposed into individual product pairs, including listing/listing comparisons so a vague canonical cannot bridge incompatible products:

- `same_product` — listings describe the same manufactured marketed product.
- `different_product` — listings contain different products or variants.
- `uncertain` — not enough evidence to distinguish them reliably.

Gemini also supplies model-reported confidence and a reason. Its confidence is **not a calibrated measure of real-world accuracy**. This is an assistant to the deterministic identity rules, not a source-of-truth for INCI lists, prices or brand alias approval.

## Queue operations

1. Generate real review cases from existing legacy identity conflicts through **Product Matching Reviews → Find legacy products needing review** if needed.
2. Select individual cases or **Send all unresolved identity cases to Gemini**. `POST /admin/aggregation/reviews/gemini/enqueue` accepts `review_ids` optionally, with a bounded queue cap up to 20,000.
3. Choose the maximum pair requests per run (default 500). Start `POST /admin/aggregation/reviews/gemini/run` with `batch_size=1`. Group batching is rejected. Every request compares two products, and completed pairs are reused when a partially completed review resumes.
4. Poll `/admin/aggregation/reviews/gemini/jobs/{task_id}`; cancel with `.../cancel`. The worker requeues orphaned interrupted processing items and leaves provider failures retryable.
5. Open **Gemini Decisions** to review saved results using confidence filtering, verdict/outcome sorting, complete/pending status, and expandable evidence. `GET /admin/aggregation/reviews/gemini/results` is read-only and does not call Gemini.

The queue uses `GEMINI_API_KEY`, `GEMINI_MODEL_NAME`, shared Gemini request limits and `pipeline_tasks` telemetry. Pair results are stored in `identity_gemini_pair_results`; local code combines them into a review outcome. One confident positive pair can identify its canonical; multiple positive canonicals remain a duplicate conflict. A distinct new source product requires every compared candidate pair to be confidently different. No model request asks Gemini to select a winner from a list.

## When an automatic identity decision is permitted

For **saved-source candidate comparisons**, Gemini selects the semantic match. Confident `same_product` decisions link the selected candidate and source offers; confident `different_product` decisions reject the supplied candidates and retain a distinct source product. The live worker and saved replay share the application policy. Missing metadata, differing retailer wording, or the absence of a second retailer do not by themselves block application. Actual attribute contradictions, stale reviewed evidence, conflicting explicit decisions and unscoped multi-variant cases remain unresolved. See [source application semantics](saved-gemini-decisions.md#retailer-source-comparisons).

The rules below describe verification of an **existing canonical cluster**, rather than linking a new retailer observation:

The default current-code threshold for `same_product` is **0.90**, **subject to all independent checks**:

- One existing canonical product only; no automatic merging of two competing canonical IDs.
- Current canonical title and every current linked source listing match the evidence snapshot reviewed by Gemini.
- Retailer brand aliases are registry-approved or explicitly curated.
- No incompatible SPF, fragrance/shade, concentration, timing, vehicle or line-level identity attribute conflict.
- No competing duplicate canonical and no other outstanding identity conflict on the same canonical.
- No incompatible trustworthy full ordered-INCI declarations.
- Sufficient independent retailer evidence for automated source identity publication (currently two independent stores).

When all these pass, the worker verifies links and canonical identity, closes the review and writes `canonical_identity_history`. If a deterministic guard blocks a high-scoring model suggestion, it remains a review case **with the reason recorded separately**. Older `different_product` cases may still contain a historical same-product-only rejection message; the read API labels this as an outdated policy result rather than claiming the model was uncertain.

A high-confidence `different_product` verdict does not authorize a same-product link. It can be **replayed as a safe audited split** only when every retailer listing can be assigned to an explicit variant group anchored to the original canonical record. Examples: NUXE Huile Prodigieuse original/Florale/Golden or different named Acti-series products. Unsafe partitioning, changing evidence or conflicting formula versions remain manual.

## Avoid rerunning stored verdicts

The newer [saved-decision replay](saved-gemini-decisions.md) permits **Preview saved decisions** and **Apply confident decisions — no Gemini calls**. It targets eligible previously evaluated `needs_manual` cases at ≥0.90, even if they had originally failed an older 0.97 policy threshold. It does not issue a provider request. Safe replay closes the case; unresolved blockers remain for review.

## Gemini Decisions table

The backend endpoint `GET /admin/aggregation/reviews/gemini/results` supports:
- `verdict`, `outcome`, `review_status` filters;
- `confidence_min`/`confidence_max` as fractions 0–1;
- free-text `search` for titles and numeric IDs;
- `sort_by` `confidence|review_id|updated_at|verdict|outcome`, `sort_order` `asc|desc`;
- `limit` 1–200 and `offset` pagination.

Each result provides model explanation, separate application reason, retailer evidence, current-evidence indicator, applied audit decision and timestamps, including already completed cases.

## Safety, monitoring and limits

- The Gemini product identity worker can be blocked by quota/key errors. A queued verdict or high score is not automatically committed until the backend record is confirmed.
- Duplicate canonical merges require the separate audited merge workflow; no automated two-canonical merge is implied.
- Model identity approval never publishes an INCI formula.
- Live provider calls and live catalog migrations were **not** executed simply by merging code or documentation. Verify locally and inspect sampled results before running the entire backlog.

[Full API](../api-reference.md) · [Database schema](../database-reference.md) · [Operations](../workflows-and-operations.md)
