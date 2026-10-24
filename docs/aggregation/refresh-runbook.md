# End-to-end refresh approval scope

Status: awaiting user confirmation; no bulk refresh started.

The pre-migration SQLite backup is
`data/backups/skincare-before-aggregation-20261024T171654Z.db` (relative to the
workspace parent of this repository). Read-only verification on 2026-10-24 found
integrity `ok`, 22,800 products and 14,710 canonical products. The adjacent
verification JSON records preserved products, canonical mappings and evidence.

After confirmation:

1. Take a fresh SQLite backup of the current migrated database and record counts.
2. Run source/API diagnostics and validate representative extraction samples.
3. Refresh the six currently enabled retailers with bounded, paced collection.
   Validate the remaining registered sources and progressively collect sources
   whose access policies and extraction support permit collection. Record blocked,
   unsupported and failed sources explicitly rather than reporting full coverage.
4. Reconcile identities, variants, prices, categories and formulation evidence.
   Route ambiguous identities, regional differences and conflicting declarations
   into review; preserve approved canonical evidence until a valid replacement exists.
5. Apply eligible updates, verify dataset integrity, matching and publication gates,
   and report before/after counts, source failures and outstanding review cases.

Admin controls: hamburger menu → Source Registry. Every card has a collection
on/off switch, and the selected-source coverage area also supports pause/enable.
Enabling collection alone does not approve publication or start a bulk refresh.
