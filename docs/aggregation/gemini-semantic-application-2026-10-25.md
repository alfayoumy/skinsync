# Gemini semantic decision application — 25 October 2026

The old source application policy required the deterministic exact-name matcher to independently prove the same identity that Gemini had been asked to resolve. It also treated inferred or missing metadata as contrary evidence, ignored source-level different-product results, and failed to materialize source offers when accepting a link. These defects are corrected.

## Applied results

At the operator's selected **95%** threshold, 715 saved cases were eligible at the start. Across two backed-up passes:

- **589** source products linked to Gemini's selected canonical.
- **70** different-product decisions applied as rejected comparisons.
- **68** distinct provisional products retained, with their saved offers.
- **56** eligible cases remain unresolved: 43 lacked an unambiguous variant scope in the original model input, and 13 encounter a possible current match that Gemini never compared.
- The other **989** original saved source results lack an actionable verdict at this threshold (uncertain, below threshold, or missing the selected canonical).

Pending identity reviews decreased from **1,704 to 1,045**. Existing prices, stock and canonical formulations were compared against the pre-application SQLite backup and **none changed**. SQLite quick check passed with zero foreign-key errors. No provider calls were made. The API was restarted with the corrected policy.

[Machine-readable report, backup paths and validation](gemini-semantic-application-2026-10-25.json).

## Current behavior

Gemini adjudicates semantic differences and chooses among the supplied candidates. Equivalent brand aliases, French vehicle names, omitted vehicle fields, body milk versus lotion, and lack of a second retailer are not independent reasons to reject a confident result. Derived taxonomy fields are compared consistently under current rules. Actual contradictory attributes and explicit prior decisions remain protected.

Same-product decisions create accepted source links, aliases, eligible product mappings and source offers. Different-product decisions reject the supplied candidates and resolve the retained source product against the remaining current catalog. A source decision never publishes INCI, merges existing canonical IDs, or overwrites current offer prices/stock with an old observation.

Future queue construction scopes each variant review to its actual shade/formulation attributes. Application only materializes the reviewed variants; it cannot assign sibling shades to the same canonical implicitly. A canonical with no identifiable target shade cannot stand in for multiple known different shades. Historical unscoped verdicts are preserved, not reinterpreted as if Gemini had received the corrected input.

The live worker and saved replay use the same application policy. Preview evaluates decisions in sequence in one rolled-back savepoint, including effects on subsequent candidate selection. Eligibility counts describe the state before the batch, and all remaining blockers are summarized with counts.

## Validation and repeatable operation

The final **109 core matching tests passed**, including scoped shade application/rejection, real SPF conflicts, semantic vehicle/function equivalence, preview rollback, idempotence, changed evidence and preservation of prices and formulas. A broader focused run passed 160 tests. The larger regression run exposed two previously reproduced real-catalog recommendation fixture failures and a category fixture that supplied an explicit Serum type while expecting uncertainty; the category fixture was corrected and its tests rerun. Frontend production build and diff checks passed.

From the repository root:

```bash
python3 api/scripts/apply_saved_identity_decisions.py --confidence .95 --output /tmp/gemini-preview.json
python3 api/scripts/apply_saved_identity_decisions.py --apply --confidence .95 --output /tmp/gemini-applied.json
```

Application refuses active catalog workers and creates a fresh SQLite backup before taking its write transaction. Model responses and source evidence are retained for inspection.
