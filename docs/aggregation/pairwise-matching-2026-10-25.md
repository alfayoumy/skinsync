# Two-product Gemini matching — 25 October 2026

Every product identity request now compares **exactly two products**. A source with six candidate products makes six separate requests. Gemini returns only the pair's verdict, confidence and reason; it never selects a winner from a list or invents a canonical ID. The server binds each response to its outbound pair and combines independent results locally.

## Enforcement

- The identity request builder rejects multiple pairs or anything other than two product objects before network access.
- `POST /admin/aggregation/reviews/gemini/run` accepts only `batch_size=1`. `limit` caps actual pair requests. Partial reviews remain queued and reuse already completed pairs.
- Each paid pair result is saved independently in `identity_gemini_pair_results`, tied to its two product identities and evidence snapshot. A failed later request does not lose earlier results.
- Canonical clusters also compare actual retailer listing/listing pairs, preventing a vague canonical name from bridging incompatible products.
- A source comparison targets one specific shade/formulation. Different source variants cannot be packed into one generic product-family comparison.
- The legacy Gate 4 retailer/reference verifier also uses one pair per request. Its worker processes one item at a time even if an old caller supplies a larger batch setting.
- Both dashboard batch selectors were removed. Results show the actual product pair and its individual verdict/application status.

Confident negative pairs are applied independently without requiring a positive match against another candidate. A confident positive source pair can identify its canonical; multiple positive canonicals remain a duplicate conflict. Creating a distinct product requires every compared candidate to be confidently different. An unresolved pair does not invalidate the other pairs' saved evidence.

## Queue migration

**1,045** unresolved legacy group reviews were prepared as **4,400** individual pair comparisons, with **zero provider calls during preparation**. Original group responses and source snapshots were archived in `identity_gemini_legacy_history`; full SQLite backups were created first. Previously applied product links were retained. Historical group responses are marked explicitly and are not eligible for new saved-decision application.

[Preparation counts and backup](pairwise-preparation-2026-10-25.json).

## Live White Musk example: review #5226

The current catalog contained six candidates. Six real provider requests were intercepted for validation; **every outbound request contained exactly two products**. Gemini returned:

| Compared against White Musk Travel | Verdict | Confidence |
| --- | --- | ---: |
| Cupcake Travel Lotion | Different product | 95% |
| Strawberries & Cream Travel Lotion | Different product | 99% |
| Bonbon Scented Travel Moisturizer | Different product | 95% |
| Bubblegum Travel Lotion | Different product | 95% |
| Vanilla Travel Lotion | Different product | 99% |
| White Musk Ultra Rich Body Lotion | Different product | 85% |

The five confident negative matches were applied. The last pair remains below the 90% application threshold: Gemini identified a possible distinction between the travel lotion and Ultra Rich formulation. No positive match or new canonical was forced. Applying the cached negative results issued **zero additional requests**.

[Actual request product counts, pair payloads, returned reasons and outcome](pairwise-white-musk-2026-10-25.json).

## Validation

192 focused regression checks passed; the final matching/transport/documentation checks and frontend production build also passed. The live API rejected `batch_size=10` with HTTP 422 and returned six two-product records for #5226, five marked rejected, with its original group response available in the archive. SQLite integrity passed with zero foreign-key errors; ingredient coverage remained unchanged at 51 published lists.

A missing canonical/status lookup index was also added for identity links. The live queue's full evidence-readiness check fell from approximately 11 seconds to 0.51 seconds without weakening its validation.

## Operations

```bash
python3 api/scripts/prepare_pairwise_identity.py --output /tmp/pair-preview.json
python3 api/scripts/prepare_pairwise_identity.py --apply --output /tmp/pair-prepared.json
```

Preparation is idempotent and makes no provider calls. Run the queued pairs separately from the dashboard using a maximum pair-request budget. The UI retains historical explanations in an archive disclosure and labels pending historical results as awaiting pair comparisons, rather than presenting their old group confidence as a new verdict.
