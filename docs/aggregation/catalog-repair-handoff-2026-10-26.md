# Catalog repair: completed changes and remaining work

Updated **27 October 2026, approximately 00:27 Cairo**. This handoff supersedes the earlier 21:00 and 23:58 count summaries. Current evidence: [fresh database, runtime and backup reconciliation](catalog-handoff-update-state-2026-10-27.json). Earlier dated reports remain historical checkpoints.

**The catalog is not fixed. Ingredient publication has regressed substantially against the pre-remediation backup.** Matching, category, extraction and projection fixes are real, but they do not compensate for incomplete recovery of existing catalog data. Recovering the historical ingredient evidence and reconciling missing mappings is now the first priority. Source edits, applied database repairs, acquired pages, staged evidence and published formulas are distinct states.

## 1. Current authoritative state

Counts come from a consistent SQLite snapshot. Frontend, backend health and authenticated admin overview returned HTTP 200 during this update; the overview agrees with the publication and visible-product counts below.

| Measure | Current state |
| --- | ---: |
| All canonical records, including historical redirects | 7,422 |
| Active canonical products | 7,416 |
| Active skincare canonicals, including hidden records | 5,766 |
| Visible skincare canonicals | 4,164 |
| Hidden skincare canonicals | 1,602 |
| Verified canonical identities, all product types | 697 |
| Canonicals with published ingredient text | 213 |
| Skincare canonicals with published ingredient text | 209 |
| Published declarations failing the current structural completeness scanner | 0 |
| Distinct source products with retained ingredient text, including partial lists | 1,256 |
| Original retailer rows | 22,155 |
| Pending identity / category / formulation reviews | 863 / 708 / 16 |
| Prepared two-product comparison rows | 4,416 |
| Completed pair verdict rows | 826 |
| Prepared comparison rows without a verdict | 3,590 |
| Saved pair payloads containing anything other than two products | 0 |
| INCI reference library entries / SkinSAFE entries | 53,017 / 50,969 |
| SQLite integrity / foreign-key violations / processing reviews | ok / 0 / 0 |

Coverage is **3.6% of 5,766 skincare canonicals**. A structurally complete string is not proof of manufacturer accuracy, correct regional formula or correct historical identity. The visible skincare count includes provisional identities and must not be described as 4,164 verified products.

### Backup reconciliation: the coverage regression is real

The on-disk `api/skincare_backup_pre_remediation.db` contains:

| Measure | Backup | Current |
| --- | ---: | ---: |
| Skincare retailer listings | 8,573 | Different scope from the visible-canonical card |
| Skincare canonical records | 5,351 | 5,766 active |
| Skincare canonical records with ingredient text | **1,665** | **209 published** |
| Skincare retailer listings with ingredient text | **6,672** | Ingredient ownership now canonical; legacy raw fields empty |
| Skincare retailer listings with a stored reference ID | 712 | Current reference-link count is a separate metric |

The old 8,573 figure counted retailer listings, including several stores selling the same product. Comparing it directly with 4,164 visible canonicals overstates product loss. **That denominator change does not explain or excuse the ingredient regression.** The reset history records removal of the old product/canonical tables and replay of 22,155 retained observations; the old ingredient assignments were not recovered into current published formulas. The backup retains the original data.

Exact-URL reconciliation of all 8,573 old skincare listings now shows:

- **4,947** map to visible skincare entities.
- **1,625** map to hidden skincare entities.
- **1,272** retain their retailer URL but lack a canonical mapping in the current product/offer tables.
- **612** old URLs are absent from current listings. Investigate changed handles, discontinued listings and missing ingestion before calling these deleted products.
- **117** map to other product types. Review their classification evidence rather than assuming all exclusions are correct.

Historical provenance labels include scraped retailer text, SkinSAFE matches, whitelist-description extraction, OpenBeauty and cluster propagation. Those labels alone prove neither correctness nor invalidity. Recover original text and lineage first, then review identity, full-list completeness, formula conflicts and market applicability. **No broad historical formula restoration has been applied.** The previously discussed full recovery-inventory artifact is not present in the current worktree; regenerate and persist it from the backup before treating it as an available deliverable. The fresh reconciliation above is persisted in the linked state JSON.

## 2. Matching fixes completed

### Exactly two products per Gemini request

- Replaced group/candidate-list matching with one physical request containing exactly two product objects. A source with several canonical candidates generates separate comparisons; the application combines the answers locally.
- Canonical clusters compare their retailer listing pairs, rather than allowing a vague canonical title to bridge incompatible listings.
- The request builder rejects multi-pair requests before network access. The identity API accepts only batch_size=1; the live API previously rejected batch_size=10 with HTTP 422.
- The legacy Gate 4 retailer/reference Gemini worker also processes one product pair at a time.
- Removed both dashboard batch selectors and misleading multi-product-call wording.
- Saved each pair independently in identity_gemini_pair_results, bound to its two product inputs and evidence hash. Requests cannot invent a canonical ID or select an unbound candidate.

### Migration, replay and application

- Archived unresolved historical group responses and source snapshots in identity_gemini_legacy_history, with full database backups. Group results are excluded from new automatic saved-decision application.
- Prepared 1,045 unresolved reviews as 4,400 pairs without provider calls. Preparation is not counted as completed matching.
- Completed pair results survive interruptions. Unchanged pairs can be reused when another candidate is added; changed inputs require a fresh pair. Application still rechecks current evidence.
- Confident negative pairs reject individual wrong candidates without requiring a positive match elsewhere. Multiple positive canonical candidates remain a duplicate conflict.
- Positive source matches materialize source offers and eligible legacy mappings, with explicit safeguards for brand, formulation attributes, competing candidates, identity evidence and previous decisions.
- Preserved previous explicit human decisions and conflicting formulations. Model confidence is not used as permission to merge everything.
- The first bounded queue run completed 319 comparisons, resolved 72 review cases and applied 264 negative candidate links before a provider failure. That was an early checkpoint; the current database contains 826 completed pair verdict rows.
- Provider errors now report credential-free status and distinguish daily quota from minute-rate failures. Successful earlier applications are not presented as lost because a later request failed.

### White Musk case and historical wrong merge

- Review #5226 was actually tested through six separate two-product requests. Five wrong scent candidates were confidently rejected. White Musk Travel versus White Musk Ultra Rich returned an 85% negative verdict, below the 90% application threshold; that distinction was not forced into a match. A historical “100% uncertain” is confidence in uncertainty, not identity proof.
- Audited 607 accepted saved-Gemini source links against current title-derived attributes. Eight were flagged at that checkpoint. This was a screening audit, not a complete historical identity validation.
- Corrected a proven historical merge: Infinity lavender body scrub and lavender/peppermint foot scrub were different products sold under separate retailer IDs, with different marketed uses and ingredient summaries.
- Split the body scrub from canonical #65192 into #70501 using the audited split workflow; rebound its own source association, rejected the old association, retained a cannot-link constraint and preserved price/stock/observation history. The new source-owned identity was explicitly reviewed and published. No ingredient formula was copied or approved by that split.

Evidence: [pairwise matching](pairwise-matching-2026-10-25.md), [White Musk payloads and outcomes](pairwise-white-musk-2026-10-25.json), [queue run](catalog-pairs-2026-10-26-batch-01.json), [historical link screening](catalog-historical-gemini-link-audit-2026-10-26.json), [Infinity split and backup](catalog-infinity-scrub-split-2026-10-26.json).


### Subsequent applied matching corrections

- Corrected the older brand gate to honor curated aliases such as PURITO Seoul/PURITO and Dermacy Labs/Dermacy, while rejecting unapproved brand prefixes. Unicode brand labels are no longer silently erased by ASCII-only comparison.
- Recognized declared sun-stick/Photoderm functions, face-and-eye anatomy and lipgloss spacing without relaxing explicit SPF mismatches or dedicated eye/lip conflicts.
- Corrected a proven saved-Gemini mistake: **Tocobo Cotton Airy Sun Stick** was linked to **Cotton Soft**. Reviewed manufacturer/regulatory evidence supports separate marketed formulations. Moved the exact Dr Dawood listing into **#70574**, rejected its old #63687 association, retained a cannot-link constraint and published the reviewed identity without copying any formula.
- Added a manufacturer-specific gate against repeating the Cotton Airy/Cotton Soft mismatch. [Audited split and evidence URLs](catalog-tocobo-airy-split-repair-2026-10-27.json).
- Split operations now revoke the moved listing's exact title alias on the old entity, preserving its audit row. Corrected stale aliases after both the Tocobo and earlier Infinity splits. [Infinity alias correction](catalog-infinity-split-alias-correction-2026-10-27.json).
- Refreshed **15 explicit unresolved pairwise cases** after new rivals appeared: **16 new pairs prepared, 37 completed answers retained**, zero provider calls. Old aggregates/source snapshots were archived; unrelated cases and paid pair answers were retained. [Applied refresh](catalog-targeted-pair-refresh-repair-2026-10-27.json). Do not reset the entire queue to refresh these cases.

## 3. Category fixes completed

- Explicit marketed functions, bounded opening product declarations and exact retailer product types now beat incidental description/ingredient/instruction keywords. Broad departments remain insufficient evidence.
- Added missing English/French product-function recognition and packaging/typography handling. Product instructions to use a different product do not define the current product's category.
- Bundles and sets are kept separate from single-formula skincare instead of automatically generating human category work for every set.
- Strong retailer hair types beat generic cream, serum and mask words. A backed-up repair removed 243 hair products from skincare classifications, while preserving specific hair-type disagreements.
- Nail serums and lash-growth serums no longer become facial serums merely because their names contain “serum.”
- “Cleansing and Regulating Toner” is treated as a toner when cleansing is a modifier; genuine wash-versus-toner conflicts remain reviewable. “Creamy wash” is recognized as a cleanser despite sunscreen/peeling mentions in instructions.
- Added explicit merchandise handling for sunglasses, teethers, soother holders, writing/activity books, foot files, ankle supports, pantyliners and reed diffusers.
- Lip gloss/lipgloss, lip liners, eye/brow pencils, brow pomades, setting sprays, color correctors and BB makeup no longer fall into toner, serum, moisturizer or sunscreen categories through incidental words.
- Bounded modifiers no longer hide anatomical roles such as hand repairing barrier cream, body musk lotion and foot scrub. A regression check prevents “The Body Shop” from turning its facial cream into body care.
- Preserved approved classifications, genuinely conflicting clusters and superseded observations instead of relabeling them to reduce the queue count.

Pending category cases fell from **4,258 at the initial repair baseline to 708 now**. Recorded successive reductions include 2,012, 798, 72, 265, 20, 6, 229 and 1 cases. Canonical projections were also repaired separately; those counts must not be added together as unique products, because later passes can revisit a record.

Recent applied projection passes corrected 19 nail/lash roles, 84 merchandise/makeup classifications, and 28 anatomical/lipgloss classifications. Earlier passes include 171, 49 and 21 projection corrections. See the dated category, hair-type, role, merchandise, creamy-wash and anatomy JSON reports for exact changes and backups.

Further repairs stripped leading brand names from function classification (notably Essence), corrected explicitly reviewed makeup products, and added bounded exact retailer types for mists, exfoliators, treatments, accessories and other roles. The latest applied pass resolved **127 category reviews (835 → 708)** and corrected three hair/beard canonicals. Explicit title functions still outrank retailer types; broad Face Gel, Oils and Body Spray departments remain insufficient. [Applied exact-type repair](catalog-specific-types-repair-2026-10-26.json).

## 4. Identity trust, brands and duplicate repairs completed

- Corrected an overbroad identity blocker: an unaccepted fuzzy source suggestion no longer invalidates an unrelated existing canonical merely because it appears among retrieval candidates.
- Accepted-association disputes, explicit canonical findings, strong-identifier conflicts and ungrounded cases still block publication. An unlinked source still cannot borrow a canonical formula.
- This scope correction independently verified 126 more corroborated identities and published 17 more saved source declarations. An earlier evidence pass verified 54 more identities and published eight declarations.
- Added an explicit, audited brand-consolidation workflow with named reviewer, source URLs and before/after snapshots. It does not merge products merely because brand spelling is similar.
- Consolidated reviewed ISISPHARMA / Isis Pharma brand labels with manufacturer/retailer evidence. Six exact product duplicate groups were then separately merged through the reversible canonical workflow; formula-bearing survivors were retained and three uncertain groups were preserved.
- Preserved historical redirects and stable canonical IDs. Current totals exclude redirected/reversed-split records from active counts.

Evidence: [identity-scope repair](catalog-identity-scope-repair-2026-10-26.json), [evidence repair](catalog-evidence-repair-2026-10-26.json), [ISISPHARMA consolidation](isispharma-brand-repair-2026-10-26.json).

## 5. Ingredient acquisition and publication fixes completed

### Corrected storage interpretation

The empty original retailer ingredient fields were partly a dashboard read-path bug: authoritative formulations belong to canonical_products, while retailer rows remain offers. Copying a canonical list into every retailer row would conceal identity mistakes. The corrected admin paths project a published formula only through an accepted, identity-verified source association.

### Saved evidence and exact retailer detail pages

- Published supported saved full declarations individually through the formulation repository, with source identity and conflict checks. All six enabled sources still have publish_enabled=0; no blanket source approval was enabled.
- Added read-only acquisition of exact known retailer URLs for verified accepted associations. This reaches ingredient tabs omitted from product feeds and makes zero Gemini calls.
- Added preview/apply retention with pre-change SQLite backups. It checks exact registered URL, current observation, product heading, verified brand, skincare role and source association.
- Preserved literal page content, URL, page hash, fetch metadata and original observation lineage in immutable source evidence. Prices, stock and identity mappings are not rewritten by ingredient acquisition.
- Complete declarations can be accepted from an otherwise partial page only when the literal ingredient section and an observed closing product heading are present. An incomplete page without a declaration cannot prove that none exists.
- Short or active-only “What's in it” summaries stay partial, including long summaries without enough formulation-base evidence. Partial lists do not become master formulas.
- Repaired installed MCP SDK compatibility with snake_case fields; exact known URLs no longer depend on a successful search, and search timeout does not discard them.
- Removed a content filter that hid ingredient tabs. Bounded continuation reads preserve chunk metadata and reject redirects, missing continuation metadata and incomplete ingredient tails. Extraction receives the retained bounded page rather than a silent short prefix.

The applied pilot plus detail batches 01–08 published **88 declarations** and retained **63 additional evidence-only lists**. Three further lists were explicitly held with durable formulation reviews. These publication totals describe applied operations, not current correctness certification. Subsequent corrections and withdrawals are recorded below; current structural scanning finds no failing publication, while manufacturer/market verification remains incomplete.

### Added withdrawal and quality protections

- Added transactional withdrawal of an invalid authoritative formulation, with named reviewer, reason, expected formula hash and a full before-state audit. Formula versions and original evidence remain retained; caller transactions are not committed accidentally.
- Withdrew PanOxyl #68556 because its saved declaration omitted the explicitly named benzoyl peroxide drug active. The inactive-ingredient list remains evidence; it is not a full formula. A formulation review remains open.
- Publication validation now treats a missing explicitly named benzoyl peroxide active as incomplete. A forced correction cannot bypass that check.
- Added detection of unfinished chemical names such as trailing “Caprylyl,” nonspecific liposomal vitamin summaries, broken compound fragments and trailing “Share:” contamination.
- Source retention and formulation readiness now respect the tokenizer's incomplete assessment instead of trusting an upstream “full” label.
- Added narrow extraction cleanup for an exact trailing Share widget, retaining the whole original fetched page for provenance. This code change does **not** mean every already published contaminated list has been corrected.
- Explicitly held two Nano lists and one Medicube list with nonspecific names, suspicious summaries or OCR damage; retained reasons and source text instead of inventing chemical corrections.
- Published the already verified Dermedic toner #68183 after its category blocker was resolved, using an audited identity-publication operation.

Evidence: numbered source-detail repair reports, [held batch 07](catalog-sourcebeauty-detail-held-07-2026-10-26.json), [held batch 08](catalog-sourcebeauty-detail-held-08-2026-10-26.json), [PanOxyl withdrawal](catalog-incomplete-drug-declaration-repair-2026-10-26.json).

### Later ingredient recovery and quality fixes

- Batch 09 is **applied**: 14 publications, 27 evidence-only lists and eight preserved cases; eleven other pages were held separately. [Applied batch 09](catalog-sourcebeauty-detail-repair-09-2026-10-26.json).
- Batch 10 acquisition is **terminal/completed**, not still running: 100 selected products, 92 retained pages and eight fetch errors. Reviewed selections across its six apply reports produced **36 publications, 15 partial evidence records and one preserved attempt**. Unselected questionable pages remain held acquisition/review artifacts, not approved formulas.
- The Bloom pilot published one reviewed SVR Topialyse declaration. [Applied Bloom repair](catalog-bloom-detail-repair-02-2026-10-27.json). The ten-page Dr Dawood pilot yielded no usable full declaration and made no ingredient writes.
- Fixed extraction boundaries so plain Brand, Size, delivery, payment and return metadata cannot be appended to ingredient declarations. Existing published formulas were also screened for these metadata patterns with no findings at that checkpoint.
- Blocked concatenated, separately labelled T-zone/U-zone formulas from publication as one ordered formula. Retained whole fetched pages and review reasons for malformed lists, suspicious product copy/paste and uncertain ordering.
- Known retailer-page extraction can now stage source evidence without Gemini. This is not automatic publication. Research review rechecks current observations and verified source association; namespaced research IDs prevent collisions with legacy evidence IDs.
- Acquisition now includes products with nonempty feed summaries when no detail declaration has yet been retained. Short feed callouts must not block fetching a full page. Already retained detail declarations are not repeatedly fetched by default.
- La Roche-Posay #64415's malformed formula was withdrawn; Eqqualberry #65115's trailing Share text was corrected with retained evidence and version history. PanOxyl #68556 remains without a replacement full declaration.

## 6. Admin dashboard, public cache and price fixes completed

- Admin overview ingredient coverage counts canonical skincare formulations once, instead of counting empty or duplicated retailer fields.
- Product ingredient/category filters and telemetry project verified canonical data, while preserving original listing values for inspection. Unverified associations cannot borrow formulas.
- Removed fixed fallback catalog totals and unsupported ingredient-concentration / first-five-ingredients claims. Ingredient source order does not establish exact percentages.
- Exposed separate visible skincare totals, all skincare canonical totals, verified identities and other product types; identity work is distinguished from category work.
- Gemini results show actual pairs, individual verdicts and application state. Old group responses are archived separately; queued comparisons do not display stale historical confidence as a fresh answer.
- Added committed catalog revisions and automatic public-cache refresh. Meaningful canonical/offer/source changes invalidate the cache; queue/no-op writes do not. Snapshot reads and locking avoid observing uncommitted changes.
- Added missing identity-link and legacy-offer indexes. A bounded offer lookup that exceeded two seconds completed in about 0.0057 seconds after indexing; a full reload measured about 0.65 seconds. A live same-process test updated visible ingredient coverage without restart.
- Health output now includes public products with ingredient text, with its public-index scope distinguished from all canonical counts.
- Price comparison now requires verified accepted source associations for both legacy and variant offers. Old canonical pointers alone no longer supply same-product offers.
- Rejected/unverified variants cannot suppress a valid legacy offer. Empty trusted-offer sets do not fall back to stale canonical price aggregates. Listing-to-canonical price resolution also checks its association.
- Price-deal results are recomputed from eligible verified offers rather than trusting historical canonical aggregates. Correction: the earlier screen incorrectly flagged 1,072 variant offers by checking root links instead of their approved hashed variant scopes. All 1,072 had verified scoped links. The migration restored those exact approvals in the projection; it did not invent new identity decisions. The 21 legacy root listings remain a separate audit concern.
- Added tests for revoked links, false cheapest offers and stale advertised savings. API dispatch tests now mock external refresh workers while checking dispatch, instead of scraping retailers during an endpoint test.

Evidence: [admin live verification](catalog-admin-live-verification-2026-10-26.json), [cache live verification](catalog-cache-live-verification-2026-10-26.json), [cache query timings](catalog-cache-query-2026-10-26.json), [later live checkpoint](catalog-live-safety-verification-2026-10-26.json).

The reference card now counts the actual **53,017-row library**, including **50,969 SkinSAFE entries**, rather than displaying the zero linked-match count as an empty library. Library inventory, linked matches and published formulas remain separate. “Multi-store products” means listed at two or more retailers; the 1,144 count is not evidence of 1,144 savings opportunities. Variant offer trust follows the exact hashed scope, and revision triggers include identity-link changes.

## 7. Work in progress: not applied or not fully validated

1. **Historical backup recovery is not applied.** The backup comparison is verified, but a complete recoverable inventory and durable source-to-current mapping must be regenerated. Do not imply the earlier 1,665 canonical ingredient records have been restored.
2. Source Beauty batch-10 held pages and fetch failures require explicit evidence handling/retries. Several lists contain malformed chemicals, trade-name summaries, absent named ingredients or apparent wrong-product text. A full-looking list alone is insufficient.
3. TIRTIR #65891 and other regional/revision conflicts still need corroboration; no indiscriminate manufacturer-list substitution was applied.
4. The Feel22 ten-page acquisition report exists; pages have not been reviewed/applied as a publication batch. Inspect its terminal report and retained pages before restarting collection.
5. Historical saved-Gemini matches remain incompletely audited. Legacy audit findings include candidate screens and false positives; the Tocobo and Infinity corrections do not establish correctness of every other match.
6. Provider daily-quota HTTP 429 was observed on the last matching run. No new provider-availability test was made for this documentation update. Refreshed pairs are prepared, not completed.
7. Frontend and backend are reachable. Later source edits still require a configured backend reload and endpoint checks before claiming that every current matching/extraction/split code change is loaded. Do not infer deployment from database changes or test results.

## 8. Prioritized remaining fixes

### P0 — recover the existing catalog before rebuilding more evidence from scratch

- [ ] Regenerate and persist a complete historical ingredient recovery inventory from `api/skincare_backup_pre_remediation.db`, including raw text, old source/reference IDs, product URLs, old canonical ownership and current mappings. Preserve unresolved records instead of dropping them.
- [ ] Reconcile old full-looking ingredient records against already verified current identities, beginning with exact retailer-source evidence and independently corroborated formulas. Keep partial or disputed records as recoverable evidence; do not blindly publish old matches or dismiss them all as unsafe.
- [ ] Investigate the **1,272 retained old skincare URLs lacking current canonical mappings** and **612 absent URLs**, including changed handles and missing/discontinued listings.
- [ ] Account explicitly for publication regression: recovered evidence, correctly mapped records, published full formulas, partial declarations and unresolved conflicts. Restore demonstrably correct old assignments in backed-up, reviewable batches.
- [ ] Maintain a per-record disposition for the old 8,573 skincare listings and 1,665 canonical ingredient records so future counts can be reconciled rather than explained only through changed definitions.

### P1 — finish identity, category and formulation recovery

- [ ] Resolve the **863 identity, 708 category and 16 formulation reviews** from current evidence. Preserve explicit human decisions, scoped variants, real rival candidates and competing formulas.
- [ ] Run/resume actual two-product comparisons with a bounded physical-request budget when provider availability permits. Preserve completed answers; the current 3,590 verdict-free rows are not 3,590 completed matches.
- [ ] Audit historical applied group-based Gemini mappings beyond title-only screens. Revalidate ingredients, offers, aliases, scopes and prices after each proven wrong-link correction.
- [ ] Review hidden skincare identities and verify that other-type exclusions are supported. Visible provisional records must not be presented as verified identities.
- [ ] Obtain replacement full declarations for PanOxyl and La Roche-Posay and resolve TIRTIR/other product-region conflicts. Review remaining acquired pages and record durable no-declaration/fetch-failure checkpoints with explicit retry policies.
- [ ] Validate manufacturer product identity, chemical naming, declaration order, named actives, shade/component ownership and regional/revision applicability throughout the catalog. Structural completeness alone cannot certify accuracy.

### P2 — finish runtime, dashboard and delivery verification

- [ ] Reload the configured API with the latest source edits and recheck actual paired matching, extraction boundaries, split aliases, ingredient telemetry, prices and current canonical offers. Preserve environment and avoid duplicate workers.
- [ ] Audit package size/unit normalization and same-size savings; never imply grams equal milliliters or that every multi-store cluster is a deal.
- [ ] Finish queue explanations and honest workflow states for old evidence, pending comparisons, provider failures, review ambiguity and missing formulas.
- [ ] Keep pre-change backups and reasoned audits for all mutations. Review the entire dirty worktree; do not revert unrelated edits, commit/push or deploy implicitly.
- [ ] Verify catalog-wide completion against correct identities/categories, recoverable old data, supported formulas or explicit missing evidence, trustworthy offers, accurate counts and preserved history. Passing tests or lower queue counts alone do not establish completion.

## 9. Validation, files and operational entry points

The latest full regression log records **508 passing tests in 75.497 seconds** on an isolated SQLite backup (`/tmp/catalog-identity-boundary-final-tests.log`). Later focused checks include **31 passing pair/refresh tests**, **52 split/aggregation tests**, and **16 identity-review tests**. These runs cover their respective code checkpoints; they are not a new full-suite run for this documentation edit. The reference-counter frontend build and focused admin tests passed at the preceding UI checkpoint.

This documentation update rechecked SQLite integrity, foreign keys, pair payload cardinality, publication structural completeness, both HTTP services and live overview counts. It does not publish formulas, repair mappings, restart services or certify all historical records. See [fresh handoff evidence](catalog-handoff-update-state-2026-10-27.json).

Key implementation files:

- Matching: api/pipeline/identity_pairs.py, product_identity_gemini.py, saved_gemini_decisions.py, canonical_identity_resolver.py, identity_trust.py, identity_gates.py; API aggregation router and Gemini review UI components.
- Categories/repair: api/pipeline/taxonomy_service.py and catalog_repair.py.
- Ingredients: api/pipeline/source_detail_ingredients.py, inci_research.py, inci_tokenizer.py, source_formulations.py and formulation_service.py.
- Dashboard/public reads: api/routers/admin.py, api/services/catalog_service.py, price_service.py, api/pipeline/catalog_revision.py, aggregation_schema.py and skinsync-app/src/AdminObservatory.jsx.
- Targeted regression modules: test_identity_pairs, test_saved_gemini_decisions, test_category_declarations, test_catalog_evidence_repair, test_source_detail_ingredients, test_formulation_withdrawal, test_admin_authoritative_catalog, test_catalog_cache_revision and test_price_identity_trust.

Run repair commands from the repository root. Default repair invocations preview and roll back; apply mode creates backups. These are entry points, not a recommendation to apply every preview without reviewing its evidence:

~~~bash
python3 api/scripts/prepare_pairwise_identity.py --help
python3 api/scripts/run_identity_pair_queue.py --help
python3 api/scripts/apply_saved_identity_decisions.py --help
python3 api/scripts/repair_catalog_categories.py --help
python3 api/scripts/repair_catalog_evidence.py --help
python3 api/scripts/fetch_known_ingredient_pages.py --help
python3 api/scripts/retain_source_detail_ingredients.py --help
python3 api/scripts/consolidate_reviewed_brand.py --help
~~~

For backend regressions, first create a consistent SQLite backup and set SKINSYNC_DB_PATH to that backup. Importing the app against the live database can collide with its instance lock; pointing at a missing temporary file can create an empty database and invalidate the test setup. Never run mutating integration tests against production by default.

Applied repair JSONs record relevant backup paths; retained backups are under api/backups/catalog/. Dated reports describe their own checkpoint, not current telemetry. `catalog-handoff-update-state-2026-10-27.json` is the current snapshot for this document.

