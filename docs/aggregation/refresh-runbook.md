# End-to-end refresh approval scope

Status: blocked by hardening release gates and independent review; no bulk refresh started.
See `hardening.md`. Configure scoped administrator access and complete labeled source
evaluations before requesting final bulk-refresh confirmation.

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

## Waterfall zero-target troubleshooting

The Full INCI Waterfall Batch Engine now preflights eligible canonical identities.
A legacy catalog can contain many skincare products but no identities independently
marked verified. The former `completed 0/0` was a misleading success: no provider
was called. The UI now shows scoped, unverified, pending-review and eligible counts;
the start endpoint returns HTTP 409 when nothing is eligible.

In the Waterfall modal, use **Preview safe identity matches** before selecting
**Certify exact matches (max 200)**. Certification is an explicit, administrator-
scoped operation. It approves only legacy canonical clusters supported by two
independent retailers whose every associated title, verified brand and formulation-
defining attributes agree, with no pending identity reviews or conflicting complete
ingredient declarations. It never overwrites INCI or mass-approves single-store
items. Review other identities individually and use the admin review workflow.

Legacy Open Beauty Facts, SkinSAFE and LangSearch matches without verified exact-
product source identities are retained as candidate evidence, not automatically
published. Verified manufacturer/retailer observations follow the controlled source
formulation resolver. Run a small dry run before enabling writes.
