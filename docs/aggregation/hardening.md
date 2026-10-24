# Hardening assessment response

Bulk refresh remains unapproved. This patch does not collect from additional sources,
reassign live identities, replace legacy formulas, or persist the old dry-run findings.

## Administrator access

All `/admin`, task and log requests require a scoped bearer credential. All other
write endpoints require credentials except onboarding and public recommendation,
formula matching and scoring requests. Administrative credentials fail closed when
absent or invalid. Allowed frontend origins default to localhost/127.0.0.1 port 5173;
set `SKINSYNC_ALLOWED_ORIGINS` to an explicit comma-separated list for deployment.
Wildcard origins are discarded. Use HTTPS when serving outside localhost.

Run once, from the repository root:

```sh
python3 api/scripts/configure_admin.py --token-file ../data/admin-access/local-admin.token
```

This appends `SKINSYNC_ADMIN_CREDENTIALS` to the ignored local API `.env`, creates a
private token file, and prints only its location. Restart the backend, open Admin,
and enter the token. The browser keeps it in memory, clears it on sign-out/unmount,
and only attaches it to the configured local API origins. No credential is bundled
with the frontend or saved to localStorage.

Credentials map authenticated names to independent `read`, `collection`, `identity`,
`formula`, `formula_correction` and `operations` permissions. Evidence approval that
can force a correction requires both formula permissions. Collection users cannot
approve identities, rollouts or formulas. Reviewer attribution comes from the
authenticated credential, overriding supplied reviewer names. Internal CLI operations
continue to require explicit reviewer/reason attribution.

Before deploying the new evaluation endpoints, stop API/workers and apply the additive
schema update with a fresh backup (this does not start collection):

```sh
python3 api/scripts/migrate_aggregation.py --backup ../data/backups/skincare-before-hardening-UNIQUE.db --apply
```

The additive hardening migration was applied locally with a new persistent backup:
`data/backups/skincare-before-hardening-20261024T181600Z.db` in the workspace.
All existing product, canonical and evidence fields were verified unchanged. A local
administrator credential is configured; its private token is in workspace
`data/admin-access/local-admin.token`. No credential is committed to Git.
Collection, reconciliation persistence and bulk refresh were not started.

## Identity and INCI trust

Historical links receive `legacy_preserved_unverified`; they are not evidence of
identity verification. Preservation does not register verified title aliases. The
resolver ignores title aliases attributable to unverified legacy links. Independently
validated exact structured links or explicit manual reviews can be identity-verified.
New identities remain hidden/provisional even when their source is approved.

Bulk waterfall requires a verified canonical identity and excludes pending identity
or duplicate cases in both review queues. At publication time the repository requires verified canonical identity and checks
again, including cases with candidate IDs and null canonical IDs. Forced corrections
cannot bypass identity conflicts. Retailer and source observation candidates require
a verified source-to-canonical link; request flags alone cannot establish identity.
Historical ingredient text is retained. The additive provenance field distinguishes
legacy-unverified formulations from approved authoritative evidence.

## Rollout evaluation

`POST /admin/aggregation/sources/{id}/evaluations` freezes a server-selected stratified
sample, current canonical/formula/mapping baseline and a hash of source policy and
matching/extraction code. Inspect `/evaluations/{id}` and submit independently reviewed
labels to `/evaluations/{id}/labels`, then finalize with `/evaluations/{id}/finalize`.

Publication gates require at least 100 distinct source products, 50 marketed identities,
10 examples each of sunscreen, serum, treatment, cleanser and moisturizer, and 50 actual
automatic links. Price/INCI sources additionally need at least 50 evaluated offers/full
ordered ingredient samples. Metrics are computed server-side. Artifacts and labels are
immutable; corrections require a new run. Rollout accepts `evaluation_id`, not metrics.
The source, code, observations, associations, offers and existing catalog baseline must
still match the evaluated state. Existing precision, false-merge, preservation and
classification thresholds also apply. These are minimum release gates, not evidence of
population-wide accuracy. Specialized sources without this category coverage remain
staged until a separately reviewed evaluation policy is implemented.

## Controlled reconciliation and real samples

`POST /admin/aggregation/reconcile` remains a disposable preview. To retain a reviewable
snapshot use `POST /reconciliation/previews`; after review, explicitly persist its findings
with `POST /reconciliation/previews/{id}/findings`. Both paths are relative to
`/admin/aggregation`. Persistence rejects stale catalog fingerprints, copies findings into
the actionable queue, and records approver and snapshot provenance. It does not apply
product reassociations or replace formulas. The historical 1,347 finding count is a preview
count, not a claim about live queue contents.

`api/scripts/sample_catalog_review.py` exports deterministic, stratified real catalog
samples read-only. The current export has 480 classification records and 480 existing
retailer/canonical pairs across 24 categories, stored in workspace
`data/reviews/hardening-20261024`. The committed manifest contains counts and hashes; the matching CSVs are in
`docs/aggregation/review-samples/`.
Ground-truth label columns are blank. Independent classification/identity labels,
critical false-merge and false-exclusion measurements, and an external INCI audit
remain outstanding; no metrics have been invented from model predictions.

## Collection and verification

The live collector and diagnostics share the same pre-redirect HTTPS/domain/port
validator. Redirects outside a registered source are rejected before an outbound
request is built. Robots checks and bounded responses remain enforced.

The regression workflow is in `.github/workflows/regressions.yml`; an added workflow
is not evidence of a completed GitHub Actions run. Local test results are recorded
in `validation-results.json`. Full refresh approval requires independent sample review,
source validation and bounded ingestion, waterfall readiness checks and verified backups.
