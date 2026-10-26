# Catalog evidence repair — 25 October 2026

The live database was backed up and repaired using retained source observations, without scraping or model calls. The [machine-readable before/after report](catalog-repair-2026-10-25.json) includes the exact backup path, outcome counts and saved-match samples.

| Measure | Before | After |
| --- | ---: | ---: |
| Canonical products | 7,230 | 7,230 |
| Verified identities | 277 | 515 |
| Published canonical ingredient lists | 0 | 51 |
| Pending category cases | 4,258 | 2,246 |
| Pending identity cases | 1,720 | 1,704 |

## Causes corrected

- Explicit French/English product functions were absent from title rules. Description keywords could determine the category even when the title identified a different function. Exact retailer types such as Face Serum and Lipstick were also treated as uncertain. Broad department labels still require evidence.
- Every identified bundle was treated as a human category decision. Bundles now retain their offers and their separate non-single-formula category without generating a mandatory category review.
- Typography in brand names and SPF ratings prevented exact independent-retailer corroboration. Equivalent formatting now compares consistently, including profiles saved before normalization changed. Meaningful variant names, concentrations, fragrance and tint remain protected.
- Source publication is disabled for every enabled source. Stored full ingredient declarations were never published. The repair individually approved 51 compatible complete ordered Egyptian retailer declarations through the existing publisher, after verifying canonical identity, source association and formula agreement. It did not enable source-wide publication or reuse historical CSV ingredients.
- The review dashboard combined category and identity work into one default list. It now defaults to identity cases and displays live coverage, separate review counts and disabled source-publication settings.

## Repeatable operation

Run from the repository root:

```bash
python3 api/scripts/repair_catalog_evidence.py --approve-staged --output /tmp/catalog-preview.json
python3 api/scripts/repair_catalog_evidence.py --apply --approve-staged --output /tmp/catalog-applied.json
```

The default previews the identical operation inside a rolled-back savepoint. Applying creates and checks a full SQLite backup, claims a write transaction, rejects active catalog workers, and preserves source policies and explicit human decisions. Omitting `--approve-staged` leaves formulation approval untouched. Model calls are never made. Product IDs are not merged, removed or recreated.

## Validation and remaining work

- 159 focused regression tests passed, including preview rollback, repeat application, independent retailer requirements, formula conflict protection, human category protection, saved Gemini replay and admin API operations.
- Three documentation inventory tests and the frontend production build passed.
- The live restarted API returned the recorded counts from `GET /admin/aggregation/health`. SQLite quick check passed, foreign-key errors were zero, and every published formula had authoritative approval evidence.
- The broader 418-test run exposed an order-dependent checkpoint test and missing documentation for the new route; both were corrected and their checks rerun. Two recommendation tests still depend on particular real-catalog titles being searchable: `&honey Cleansing Balm Clear 90g` and `Anua Peach 70% Niacin Serum 30Ml`. Do not make hidden products visible or weaken matching merely to satisfy those fixture assumptions.

This is a verified repair of supported cases, not a claim of complete catalog recovery. There are still 1,704 identity cases and 2,246 category cases awaiting evidence or a decision. Ingredient coverage remains 51/7,230. Most products have no retained full ingredient declaration, and most identities lack independent retailer corroboration. High Gemini confidence does not supply missing variant evidence. Additional source evidence and reviewed publication policy are required for broad ingredient coverage.
