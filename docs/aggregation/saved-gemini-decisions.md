# Apply previously saved Gemini identity verdicts

**Status:** Available in the Product Matching Reviews UI and FastAPI, 25 October 2026. No new Gemini API requests are issued by the preview or apply action.

## Motivation

Older product-review jobs left many `same_product` verdicts with 90–95% Gemini confidence in `needs_manual` because the old 0.97 automatic threshold blocked them. Other cases had very confident `different_product` verdicts but no automatic correction procedure. Do not evaluate all 4,000+ previously scored cases again just to change the post-processing policy.

## Available controls and endpoints

| UI | Endpoint | Result |
|---|---|---|
| Preview saved decisions | `POST /admin/aggregation/reviews/gemini/saved/preview` | Re-run deterministic application checks in reversible SQLite savepoints, no writes or provider calls |
| Apply confident decisions | `POST /admin/aggregation/reviews/gemini/saved/apply` | Tracked background application task, no provider calls |
| Track progress | `GET /admin/aggregation/reviews/gemini/saved/jobs/{task_id}` | Processed, applied, split, manual and error counts |
| Stop | `POST /admin/aggregation/reviews/gemini/saved/jobs/{task_id}/cancel` | Stop request; committed prior checkpoints retained |
| Review completed verdicts | `GET /admin/aggregation/reviews/gemini/results` | Paginated model and application history, including resolved cases |

Example request:

```json
{"limit":5000,"confidence_threshold":0.90}
```

Threshold input is 0.90–1.00; case limit is 1–20,000. The preview returns `no_gemini_calls=true` and `stored_decisions_checked`, `automatically_verified`, `automatically_split`, `still_needs_review` and sample explanations.

## Exact application semantics

**Same product:** a stored `same_product` ≥threshold is eligible for automatic verification *only if* the canonical, brands, all listed retailer evidence, variant gates, full INCI consistency and duplicate-canonical checks still pass. The backend writes accepted source links, verified identity status, closes the case and records canonical audit history.

**Different products:** a stored `different_product` ≥threshold is eligible for automatic split *only when explicit formulation-defining marker groups make a fully anchored partition safe*. It calls the audited canonical split service, assigns linked legacy listings to new **provisional** canonicals, records cannot-link constraints and audit history, and does **not** copy old INCI. Without a safe partition the case remains manual, even at 100% model confidence.

**Stale/conflicting/uncertain:** unrecognized, missing, changed-hash or conflicting records are left unresolved with specific deterministic reasons. Neither the model nor the replay code is allowed to guess missing retail identity mappings.

`identity_gemini_queue` records model verdict and application status separately; `product_review_queue` remains the operator decision queue. Applied cases remain accessible in **Gemini Decisions**; they are not charged or applied twice.

## Operational practice

1. Back up SQLite consistently under WAL.
2. Preview saved verdicts at 90% with a modest limit and inspect the sample reasoning and original retailer listings.
3. Apply, monitor job progress and confirm the `automatically_verified` / `automatically_split` counts, and check canonical history.
4. Browse the **Gemini Decisions** table, filter to high-confidence `different_product` or `needs_manual`, and examine actual unresolved partitions.
5. Keep distinct INCI waterfall and INCI Gemini queues disabled unless intentionally running ingredient work.

**Implementation:** `api/pipeline/saved_gemini_decisions.py`, `api/routers/aggregation.py`, `skinsync-app/src/AggregationReviewPanel.jsx` and `GeminiDecisionsTable.jsx`.

[Gemini workflow](gemini-product-identity-review.md) · [API](../api-reference.md) · [Operations](../workflows-and-operations.md)
