# Catalog recovery — evening follow-up, 26 October 2026

This supersedes the changing counts and open P0 items in the [21:00 handoff](catalog-repair-handoff-2026-10-26.md). Snapshot: **2026-10-26T23:10:58.665528+03:00**. The full-catalog goal remains incomplete.

## Applied since the handoff

### Published ingredient corrections

- Withdrew #64415 La Roche-Posay's malformed mixed-content declaration with a named reviewer, expected formula hash and complete audit snapshot. Its raw evidence and old formula version remain retained; a formulation review requires an exact replacement.
- Corrected #65115 Eqqualberry from its retained exact source page, removing only the trailing Share widget. The whole original page remains in provenance.
- Fixed a real republication defect found by the correction preview: withdrawal left historical version 1, but publication attempted to reuse version 1. New versions now allocate above retained history. A regression test checks withdrawal followed by publication preserves versions 1 and 2 and caller transaction ownership.
- The newest structural completeness scan reports **zero failing published declarations**. This is a syntax/completeness gate, not a manufacturer-level certification of every formula.

Evidence: [quality correction and backup](catalog-published-quality-repair-2026-10-26.json).

### Additional matching

The provider accepted an initial one-pair availability check. The following bounded queue job made **499 physical request attempts**, each containing exactly two products, before the provider returned **daily-quota HTTP 429** again:

- **110 identity review cases resolved**.
- **390 wrong candidate links rejected**.
- **66 cases remained for review or fresh evidence**.
- Completed pair results were preserved; unfinished comparisons remain queued. The final request failure does not undo earlier successful decisions.
- The task is terminal, with no processing review row or running catalog task left behind. Do not immediately retry the same daily-quota failure.
- A later guarded, zero-model evidence repair verified **three additional independently corroborated identities**; it did not force the remaining saved decisions.

Twenty remaining saved cases acquired a new plausible canonical candidate after their comparisons. They need a **targeted fresh comparison**, preserving unchanged cached pairs. One additional saved positive conflicts on cream versus gel. These are concrete application reasons, not permission to accept all high-confidence answers.

A current-hash screening audit found no accepted source link contradicted by an eligible confident negative pair. This narrow check does not replace the full historical group-match audit.

Evidence: [availability check](catalog-pair-quota-evening-recheck-2026-10-26.json), [completed job and outcomes](catalog-pairs-evening-2026-10-26-batch-01.json), [post-pair evidence repair](catalog-post-pair-evidence-repair-2026-10-26.json), [pair/link screening](catalog-current-pair-link-audit-2026-10-26.json).

### Category root causes and repairs

- Fixed the brand/function collision that interpreted the cosmetics brand **Essence** as a toner. A leading brand is removed from product-function matching; established hair-brand context remains protected. Actual skincare essences still classify as toners.
- Peer consistency checks now receive each observation's brand instead of reintroducing the same brand-word error.
- Added explicit roles for brow gels, lip/cheek makeup, lip pens and markers, liners, highlighters, skin tints, setting sprays, false lashes and cosmetic accessories. Identified sets remain separate from single-formula skincare.
- Lip treatments/serums and anatomical care are distinguished from generic facial vehicles; nail balms no longer inherit facial categories.
- The brand/function repair corrected **80 canonical classifications** and resolved **16 category cases**. The decorative-role repair corrected **58 more classifications** and resolved **four more cases**. These are operation counts, not a deduplicated product total.
- Explicitly reviewed **six remaining Essence products** against retained retailer types and, where needed, manufacturer descriptions. Soft Touch Bouncy Glow is a highlighter, and Jelly Grip Aloe Spray is a makeup prep/set product. No regional ingredient lists were copied from those pages.
- Fixed manual category review so vertical, subcategory, application area and non-skincare usage fields agree with the chosen category, with named-reviewer evidence retained.
- Re-ran category projection after the matching job and corrected one new lip/cheek cream that the long-lived worker had created using its older classification code.

Primary evidence for the two manufacturer reviews: [Soft Touch Bouncy Glow](https://essencemakeup.com/products/soft-touch-bouncy-glow), [Jelly Grip Aloe Spray](https://essencemakeup.com/products/jelly-grip-refreshing-aloe-spray).

Applied reports: [brand/function](catalog-brand-function-repair-2026-10-26.json), [decorative roles](catalog-decorative-role-repair-2026-10-26.json), [explicit reviews](catalog-essence-reviewed-category-repair-2026-10-26.json), [post-job projection](catalog-post-pair-category-repair-2026-10-26.json).

### Ingredient batch 09

- Reacquired all **60 pages**: the earlier temporary file had disappeared. Whole pages now live in a durable workspace artifact, with selected-product manifest, SHA-256 and atomic progress writes.
- Reviewed the fresh pages, applied **14 complete source declarations**, retained **27 partial/evidence-only lists**, and preserved eight pages without applying them.
- Retained **11 questionable declarations** as unverified evidence with specific formulation reviews. This includes the TIRTIR retailer/manufacturer discrepancy, nonspecific vitamin summaries, broken compounds, OCR damage and unresolved footnotes.
- TIRTIR's suspect retailer formula was not published. Manufacturer replacement still requires the exact regional/revision evidence.
- Both ingredient and category previews now run on consistent isolated SQLite snapshots. A category preview exposed a live-writer lock conflict; that failed preview rolled back, and the snapshot implementation prevents that contention. Apply still uses guarded write transactions and backups.

Evidence: [durable fetch manifest](catalog-sourcebeauty-detail-refetch-09-2026-10-26.json), [review plan](catalog-sourcebeauty-detail-review-plan-09-2026-10-26.json), [applied declarations](catalog-sourcebeauty-detail-repair-09-2026-10-26.json), [held evidence](catalog-sourcebeauty-detail-held-09-2026-10-26.json).

### Normal admin ingredient research now supports zero-model extraction

- The normal research worker now recognizes complete literal declarations on an exact current, verified Egyptian retailer association before calling Gemini.
- It reuses the detail-page heading, brand, completeness, source-link, skincare-role and variant safeguards. Multiple active source associations cannot use this path.
- It retains the page and hash, records the method as known_retailer_detail, leaves model name/confidence empty, and stages unverified evidence for **explicit review**. It does not auto-publish a formula.
- Approval rechecks the current source association and latest observation. A revoked link or superseded observation blocks publication.
- Generic model-research records now use research-prefixed source-record IDs, preventing evidence/canonical IDs from accidentally resolving as unrelated legacy retailer IDs.
- The dashboard labels literal retailer extraction as **no model**, retaining its warning that confidence is an uncalibrated ranking score.
- A real Anua page from this catalog passed the normal worker on an isolated database snapshot with **zero model calls** and remained pending review. Production was not changed by that validation.

Evidence: [real-page workflow validation](catalog-known-page-research-validation-2026-10-26.json).

### Search, recommendations, prices and runtime

- Search/recommendation projections now share verified-offer selection with the price service. Unverified legacy/variant pointers cannot supply fallback buying URLs, stock, prices or listing images.
- Trusted offer prices replace stale canonical aggregates in public projections. Without an eligible verified offer, no buying URL or invented price is supplied; the canonical identity is retained.
- Added committed revision triggers for association revocation/restoration, source-product identity, price and currency changes. Revoking a link refreshes the cache and removes its offer without hiding the canonical identity.
- The migration installed **15 revision triggers**. A measured public-cache reload took about **0.30 seconds**. The migration checkpoint found 423 visible skincare canonicals without verified eligible offers; this identifies remaining association work, not 423 proven wrong products.
- Restarted the configured API and frontend. Health, admin overview, corrected telemetry, separated Infinity scrub offers, price deals and pair results were checked. The frontend returned HTTP 200; this is not a claim of a complete visual dashboard review.

Evidence: [offer/cache migration](catalog-public-offer-trust-migration-2026-10-26.json), [live verification](catalog-evening-live-verification-2026-10-26.json), [detail checks](catalog-evening-detail-verification-2026-10-26.json).

## Current snapshot

| Measure | Value |
| --- | ---: |
| Active canonicals | 7,415 |
| Active skincare canonicals, including hidden | 5,771 |
| Visible skincare canonicals | 4,163 |
| Verified identities | 696 |
| Published ingredient text, all / skincare | 176 / 172 |
| Current published structural-quality findings | 0 |
| Pending identity / category / formulation cases | 863 / 835 / 16 |
| Completed pair-result records / still prepared | 826 / 3,574 |
| Non-two-product pair payloads | 0 |
| Running tasks / processing identity reviews | 0 / 0 |
| Integrity / foreign-key violations | ok / 0 |

Counts distinguish retained historical records, active products, visible products and published text. New distinct source-owned canonicals were created by guarded matching; an increased canonical count is not evidence of a duplicate merge. Ingredient coverage remains low: **5,599 active skincare records still lack published ingredient text**.

Validation: **500 backend tests pass**, frontend build passes, source-quality scanner reports no current structural failures, SQLite integrity passes and foreign-key violations are zero. These checks do not establish complete catalog accuracy. See [state snapshot](catalog-evening-state-2026-10-26.json) and [validation](catalog-evening-validation-2026-10-26.json).

## Next work, in order

1. Continue exact source detail acquisition **after canonical #65934**, while revisiting prior fetch failures/incomplete pages and applying individual quality review. Use durable page files rather than temporary files. Other enabled retailers and exact manufacturer evidence still need expansion.
2. Resolve the **16 formulation reviews**, especially La Roche-Posay and PanOxyl replacements and the eleven held batch-09 declarations. Existing raw text is not proof of a complete accurate formula.
3. Add a bounded targeted evidence-refresh/requeue path for the twenty stale-candidate comparisons. Preserve pair caches, explicit reviews and applied decisions; do not reset the whole queue. The current queue_cases helper excludes needs_manual entries.
4. Resume unfinished pairs only after provider availability changes. The latest actual failure is daily-quota HTTP 429; no more immediate retries are justified.
5. Complete the historical applied group-match audit and the source-owned association/orphan-canonical audit. Public records without verified offers need explicit presentation and review; absence of a verified offer must not be presented as a free product or proof of actual stock exhaustion.
6. Audit stale derived identity attributes after taxonomy changes. Category repair deliberately did not rewrite identity snapshots or invalidate all paid comparisons. Refresh evidence deliberately where old function metadata is misleading, preserving meaningful shade, formulation and variant distinctions.
7. Finish the remaining category cases, brand aliases (including Infinity/Infinity Naturals), duplicate groups, bundle quantities and gram-versus-milliliter normalization. The Dermatique set with pack_count=1 still needs quantity evidence.
8. Test the new normal research path in the deployed admin flow with an explicitly selected case, review its evidence and verify approval/cancellation/retry behavior. Existing tests and isolated real-page validation do not replace that full UI exercise.
9. Continue the broader checklist in the original handoff, including manufacturer/region/revision formula checks and field-level source trust in other admin/recommendation read paths. Do not mark the full catalog complete merely because the tests pass.


### Correction after exact variant-scope verification
The earlier 423 products without eligible offers was a projection bug: it checked root links instead of exact hashed variant scopes. All 1,072 flagged variant offers had existing verified scoped links. The corrected projection restores those offers; visible products without eligible offers: **0**. No new identity approvals were invented. See `catalog-scoped-offer-trust-repair-2026-10-26.json`.

The INCI reference card also used the linked-match count (0), rather than library inventory (53,017 entries, including 50,969 SkinSAFE entries). The counter is corrected; these entries are not published product formulas. Five focused admin tests and the frontend build pass.


### Detail recovery follow-up (first 20 pages of batch 10)
Reviewed the first 20 retained Source Beauty pages. Applied 13 selected pages: **11 published full declarations and 2 partial evidence records**. Published skincare formulas increased from **172 to 183**. Five malformed or questionable declarations were held (including broken chemical names, trailing-comma truncation, and questionable mixture ordering); two pages had no usable validated section. The larger read-only fetch continues. See `catalog-sourcebeauty-detail-review-plan-10-first-2026-10-26.json` and `catalog-sourcebeauty-detail-repair-10-first-2026-10-26.json`.

Corrected detail-page selection so short nonempty feed ingredient summaries do not block fetching full product declarations. The old condition skipped 30 Bloom, 2 Dr Dawood and 1 Source Beauty verified missing-formula products (overlapping retailer counts; eligibility still requires exact current identity and unscoped variants). Existing retained detail pages remain excluded from repeated acquisition.

Read-only reference inventory scanned all **53,017 rows**, finding **971** missing skincare canonicals with exact-name candidates (834 provisional and 137 verified). **149** have a SkinSAFE candidate, including 42 verified identities. This is candidate discovery only: reference rows lack market and per-product source URLs, and historical `store_verified` rows cannot independently corroborate this catalog. No formulas or identities were approved from name matching. See `catalog-reference-recovery-inventory-2026-10-26.json`.


### Specific retailer categories and second ingredient review
Added bounded exact retailer types for mist, exfoliator, treatment, merchandise, perfume, makeup and hair roles. Broad ambiguous types such as Face Gel, Oils and Body Spray remain unresolved; explicit titles still take precedence. Applied **127 category review resolutions (835 → 708 pending)** and corrected **three** hair/beard canonicals. Evidence: `catalog-specific-types-repair-2026-10-26.json`.

Reviewed batch-10 page indices 20–33: **7 more published declarations and 1 partial evidence record**, bringing skincare formulas to **190**. Held the dual-component AXIS-Y cream, apparent Rice Cream/white-willow copy error, Birch/Rice formula mismatch and other questionable lists. See `catalog-sourcebeauty-detail-review-plan-10-second-2026-10-26.json` and `catalog-sourcebeauty-detail-repair-10-second-2026-10-26.json`.

The 10-page Dr Dawood pilot returned no usable full ingredient declaration; no catalog writes were made from it. More acquisition there is deferred in favor of sources with complete declarations. The Source Beauty batch remains running and a bounded Bloom pilot is collecting pages, including products previously skipped because their feed summaries were nonempty.

Validation: **46 relevant tests pass**, covering category declarations, evidence repair, catalog workflows and source detail ingredient retention (`/tmp/catalog-recovery-followup-tests.log`). Both new read-only inventory/acquisition scripts compile. No Gemini calls were made in this follow-up.


### Follow-up checkpoint at 23:58 Cairo
A third reviewed selection published **5** additional declarations. This follow-up has now published **23** skincare formulas in total (**172 → 195**) and retained three partial ingredient records. All applied selections have backup paths and source-page review plans. The active skincare denominator is now **5,765** after category corrections; source listings and canonical entity totals are unchanged.

Added and tested a publication guard against concatenating separately labelled T-zone and U-zone formulas into one ordered list. Six formulation-withdrawal/publication tests pass; all **199 currently published formulas** (195 skincare) pass the current structural completeness scanner. This scanner does not prove manufacturer accuracy. The API was restarted with the guard loaded.

Current snapshot: `catalog-recovery-followup-state-2026-10-26.json`; live dashboard proof: `catalog-recovery-followup-live-2026-10-26.json`. SQLite integrity is OK, foreign-key violations are zero, and no identity review is stranded in processing. Remaining reviews: **863 identity, 708 category, 16 formulation**. The catalog goal remains incomplete.
