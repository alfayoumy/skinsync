# Catalog repair: completed changes and remaining work

> **Latest follow-up:** [evening repairs, current counts and revised next actions](catalog-repair-evening-progress-2026-10-26.md). The table and runtime observation below are the earlier 21:00 snapshot. Both published P0 declaration problems recorded here have since been corrected or withdrawn.

Updated 26 October 2026. Database snapshot checked at approximately **21:00 Cairo / 18:00 UTC**. This is the consolidated handoff for the catalog recovery work across 25–26 October, replacing earlier checkpoint counts.

**The entire catalog is not fixed.** The matching architecture, many categories, ingredient acquisition, dashboard projections and several integrity failures have been repaired. Ingredient coverage remains low, historical matches are not fully audited, and, at this checkpoint, two problematic declarations still required correction; the evening follow-up records their resolution. Source-code changes, applied database changes and a running deployment are separate states.

## 1. Current authoritative state

Source: [handoff database audit](catalog-handoff-state-2026-10-26.json). Counts below come from the current SQLite database, not dashboard placeholders.

| Measure | Current state |
| --- | ---: |
| All canonical records, including historical redirects | 7,349 |
| Active canonical products | 7,343 |
| Active skincare canonicals, including hidden records | 5,816 |
| Visible skincare canonicals | 4,245 |
| Verified canonical identities | 693 |
| Canonicals with published ingredient text | 163 |
| Skincare canonicals with published ingredient text | 159 |
| Known published declarations failing the newest quality checks | **2 — immediate follow-up required** |
| Distinct retailer source products with retained ingredient text, including partial lists | 1,153 |
| Original retailer rows | 22,155 |
| Original retailer rows with copied raw ingredients | 0 — intentionally canonical-owned |
| Pending identity reviews | 973 |
| Pending category reviews | 855 |
| Pending formulation reviews | 4 |
| Gemini queue: auto-resolved / needs manual / queued | 749 / 34 / 939 |
| Prepared two-product comparisons | 4,400 |
| Completed pair verdicts | 327 |
| Prepared comparisons without a verdict | 4,073 |
| Saved pair payloads containing anything other than two products | 0 |
| SQLite integrity / foreign-key violations | ok / 0 |

The 163/159 figures are **stored publication counts**, not proof that every declaration is chemically correct or manufacturer-corroborated. Approximately 2.7% of active skincare canonicals currently have published ingredient text. The latest scanner identifies two published records requiring correction, detailed below.

**Runtime:** localhost:8000 was unreachable during this handoff check, and no uvicorn process was found. Earlier successful API verification is retained in [live safety verification](catalog-live-safety-verification-2026-10-26.json), but it is a historical checkpoint. Do not describe the dashboard as currently live or claim the most recent source edits are loaded until the configured backend is started and checked again.

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
- The first bounded queue run completed 319 comparisons, resolved 72 review cases and applied 264 negative candidate links before a provider failure. The current database contains 327 completed pair verdicts in total.
- Provider errors now report credential-free status and distinguish daily quota from minute-rate failures. Successful earlier applications are not presented as lost because a later request failed.

### White Musk case and historical wrong merge

- Review #5226 was actually tested through six separate two-product requests. Five wrong scent candidates were confidently rejected. White Musk Travel versus White Musk Ultra Rich returned an 85% negative verdict, below the 90% application threshold; that distinction was not forced into a match. A historical “100% uncertain” is confidence in uncertainty, not identity proof.
- Audited 607 accepted saved-Gemini source links against current title-derived attributes. Eight were flagged at that checkpoint. This was a screening audit, not a complete historical identity validation.
- Corrected a proven historical merge: Infinity lavender body scrub and lavender/peppermint foot scrub were different products sold under separate retailer IDs, with different marketed uses and ingredient summaries.
- Split the body scrub from canonical #65192 into #70501 using the audited split workflow; rebound its own source association, rejected the old association, retained a cannot-link constraint and preserved price/stock/observation history. The new source-owned identity was explicitly reviewed and published. No ingredient formula was copied or approved by that split.

Evidence: [pairwise matching](pairwise-matching-2026-10-25.md), [White Musk payloads and outcomes](pairwise-white-musk-2026-10-25.json), [queue run](catalog-pairs-2026-10-26-batch-01.json), [historical link screening](catalog-historical-gemini-link-audit-2026-10-26.json), [Infinity split and backup](catalog-infinity-scrub-split-2026-10-26.json).

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

Pending category cases fell from **4,258 at the initial repair baseline to 855 now**. Recorded successive reductions include 2,012, 798, 72, 265, 20, 6, 229 and 1 cases. Canonical projections were also repaired separately; those counts must not be added together as unique products, because later passes can revisit a record.

Recent applied projection passes corrected 19 nail/lash roles, 84 merchandise/makeup classifications, and 28 anatomical/lipgloss classifications. Earlier passes include 171, 49 and 21 projection corrections. See the dated category, hair-type, role, merchandise, creamy-wash and anatomy JSON reports for exact changes and backups.

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

The applied pilot plus detail batches 01–08 published **88 declarations** and retained **63 additional evidence-only lists**. Three further lists were explicitly held with durable formulation reviews. These publication totals describe applied operations, not current correctness certification. One published list was subsequently withdrawn, and additional quality findings remain open.

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
- Price-deal results are recomputed from eligible verified offers rather than trusting historical canonical aggregates. An audited screen found 21 mapped legacy listings and 1,072 variant offers without verified links; those counts do not prove every such listing was a wrong product.
- Added tests for revoked links, false cheapest offers and stale advertised savings. API dispatch tests now mock external refresh workers while checking dispatch, instead of scraping retailers during an endpoint test.

Evidence: [admin live verification](catalog-admin-live-verification-2026-10-26.json), [cache live verification](catalog-cache-live-verification-2026-10-26.json), [cache query timings](catalog-cache-query-2026-10-26.json), [later live checkpoint](catalog-live-safety-verification-2026-10-26.json).

## 7. Work in progress: not applied or not fully validated

1. **Source Beauty batch 09 is fetched but unapplied.** All 60 selected pages were fetched; cursor ends at canonical #65934. Original preview: 25 proposed publications, 27 evidence-only, eight preserved. Twelve proposed publications were subsequently withheld for review. The reviewed preview has 13 proposed publications, 27 evidence-only and eight preserved. No batch-09 apply report exists.
2. The twelve held pages remain acquisition/preview artifacts, not an applied durable held-evidence batch. Several have uncertain chemical naming, footnotes/UI contamination or suspicious ingredient content. They need individual evidence handling, not blanket approval.
3. [TIRTIR's own Ceramic Cream page](https://tirtir.global/products/ceramic-cream?variant=45772633735387) materially differs from the Source Beauty list for #65891. Manufacturer evidence must be retained and regional/revision compatibility checked before replacing the retailer candidate. A syntactically plausible list is not sufficient identity/formulation proof.
4. The historical link audit predates the Infinity split and the last anatomical fixes. Re-run it; its eight initial flags are not eight currently proven mismatches.
5. The latest quality, Share-cleanup and anatomical source edits require a fresh configured API start and endpoint verification. Earlier successful live checks cannot prove the stopped backend has loaded them.

Batch evidence: [fetch 09](catalog-sourcebeauty-detail-fetch-09-2026-10-26.json), [original preview](catalog-sourcebeauty-detail-preview-09-2026-10-26.json), [reviewed preview](catalog-sourcebeauty-detail-preview-09-reviewed-2026-10-26.json).

## 8. Prioritized remaining fixes

### P0 — correct currently published bad declarations

- [ ] **#64415 La Roche-Posay Anthelios UVMune Oil Control Tinted Gel-Cream:** currently published text includes split chemical names and unrelated Arabic marketing/instructions. Withdraw or quarantine it with the new audited withdrawal operation. Obtain an exact product/region/revision declaration; do not silently reconstruct the chemicals or assume another Anthelios variant is equivalent.
- [ ] **#65115 Eqqualberry Vitamin Illuminating Cream:** current raw publication ends with Share UI text. Correct from the retained literal source using the narrow cleanup, record new evidence/publication, and preserve the old audit. This correction has not been applied.
- [ ] Re-run the quality scan over every published formulation after corrections. Zero syntax findings still does not prove chemical accuracy; audit wrong-product and active-only lists against better evidence.

### P0 — restore and verify the configured runtime

- [ ] Establish which backend instance/environment should serve the dashboard. The local API was unreachable at handoff; do not start duplicate workers or treat a stale process file as a running server.
- [ ] Check pending restore/reset state and real running workers before starting the configured API. Preserve secrets and deployment environment.
- [ ] Recheck health, admin overview, product ingredient filters/telemetry, pair details, price comparisons and price deals. Verify new canonical #70501 and separated scrub offers.
- [ ] Confirm latest source edits and frontend build are actually loaded; repeat a visual dashboard check when running.

### P1 — complete evidence-backed ingredient recovery

- [ ] Individually review/apply supported batch-09 pages and durably retain its held/rejected evidence. TIRTIR and other suspicious lists need manufacturer or exact package corroboration, not approval based on list length.
- [ ] Continue known Source Beauty URLs after #65934. Revisit earlier fetch errors and incomplete/no-declaration pages: advancing a cursor must not silently skip unresolved products.
- [ ] Expand the same verified, provenance-preserving acquisition to the other enabled retailers and useful official sources. Most skincare products still lack a complete published formulation: 5,657 active skincare records have no published ingredient text at this snapshot.
- [ ] Integrate deterministic exact-source ingredient extraction into the normal admin research workflow. It currently exists primarily in repair CLIs; normal research still depends on Gemini extraction after fetching pages.
- [ ] Record durable no-declaration/fetch-failure checkpoints and explicit retries, so absence, incomplete fetch and exhausted selection cannot be confused.
- [ ] Review unverified single-source identities through an explicit source-owned identity workflow or additional corroboration. Do not remove independent evidence checks merely to raise publication coverage.
- [ ] Audit source section parsing, generic vitamin/trade-name summaries, OCR/typos, active-versus-inactive drug sections, comments/footnotes and regional formula revisions throughout the existing publications.

### P1 — finish matching and historical repair

- [ ] Resume the 4,073 unfinished pairs with a bounded request budget after checking provider availability. The last actual provider check returned daily-quota HTTP 429; that is historical evidence, not proof the quota remains exhausted at handoff time. Reuse completed pairs and count physical requests.
- [ ] Resolve the remaining 973 identity cases from current evidence. Queued cases and genuine human ambiguity must remain distinguishable.
- [ ] Audit all historical applied group-based matches, not only the 607 links screened by title attributes. Verify current source identity, variant, formulation, candidate competition, prices and copied associations. Apply reversible corrections for proven mistakes.
- [ ] Revisit White Musk Travel versus Ultra Rich using exact product evidence. The 85% negative pair is not a confident accepted match.
- [ ] Review remaining brand splits, including Infinity versus Infinity Naturals and other candidate aliases, using authoritative evidence. The two Infinity foot-scrub canonicals (#65192 and #67139) must not be merged merely on spelling before brand identity is resolved.
- [ ] Resolve real duplicate groups with audited pairwise review. Preserve distinct scents, SPF, active strengths, shades, regions and product roles.
- [ ] Review bundles/multipacks separately from single-unit comparisons. Dermatique #67027 includes a source set whose handle says “2 for special price” but whose offer pack_count is 1; verify its actual contents/quantity before presenting savings or normalizing unit price.

### P2 — complete categories and downstream consistency

- [ ] Resolve the 855 remaining category cases, including unclear names, old observations and contradictory retailer clusters. Re-fetch or review evidence rather than inventing a role.
- [ ] Inspect remaining bundle/function inconsistencies, including Skin1004 Double Cleansing Duo versus Duo Set. Distinguish multi-product sets from actual single formulations.
- [ ] Review canonical #69743 Hepta Panthenol Rich Lotion against its body-lotion listings; generic title classification still creates a function mismatch.
- [ ] Audit public search/recommendation image, URL, stock and price projections for remaining reliance on unverified legacy pointers. The price service fix does not prove every public-cache field or admin sibling-offer query has been corrected.
- [ ] Ensure identity-link trust changes themselves invalidate any cache that starts depending on those links; the existing revision triggers primarily watch canonical, product, offer and source fields.
- [ ] Audit package normalization and same-size savings. Historical gram-to-milliliter equivalences and overall cross-size price ranges need evidence and must not imply equal density or equal package contents.
- [ ] Finish admin workflow explanations and progress/error states against the running dashboard, including stale/queued/uncertain confidence, genuinely unsupported cases and precise coverage denominators.

### P2 — finish recovery delivery and verification

- [ ] Keep pre-change backups and reasoned audit records for all remaining mutations. Retain reverse-operation safety against intervening changes.
- [ ] Review the complete dirty worktree, including the database that was already dirty at task start. Do not revert unrelated work, commit/push or deploy implicitly.
- [ ] Extend meaningful regression checks for newly discovered failures; re-run required backend checks and frontend build for further UI changes.
- [ ] Verify the whole catalog against an explicit completion checklist: supported identities or honest unresolved states, correct categories, source-backed complete formulas or explicit missing evidence, trustworthy offers, accurate dashboard projections, resumable queues and preserved history. Green unit tests alone cannot establish full-catalog correctness.

## 9. Validation, files and operational entry points

The fresh full regression run passed **488 tests in 59.876 seconds**, using an isolated consistent SQLite backup. It includes the latest anatomical, declaration-quality and Share-cleanup changes. All local handoff links, snapshot count checks and git diff whitespace checks pass. See [handoff validation](catalog-handoff-validation-2026-10-26.json). The frontend production build passed at earlier repair checkpoints; it was not rebuilt for this documentation-only handoff. The current backend was unreachable, so this test result is not a live deployment check or proof of complete catalog correctness.

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

All applied repair JSONs include the relevant backup paths; retained backups are under api/backups/catalog/. Dated reports describe their own checkpoint, not current telemetry. The handoff-state JSON is the current snapshot for this document.


Correction: the earlier 1,072 variant offers flagged as unverified actually had verified hashed-scope links. The projection now uses those exact approvals. See the correction in `catalog-repair-evening-progress-2026-10-26.md`.


Latest follow-up snapshot (23:58 Cairo): **195 published skincare formulas; 708 pending category reviews; 863 pending identity reviews; 16 pending formulation reviews**. See `catalog-recovery-followup-state-2026-10-26.json` and the evening progress document for the 23 newly published declarations, reference inventory, and corrected variant-scope audit. Earlier snapshots are historical.
