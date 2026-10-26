# SkinSync Admin 2.0 — Resumable Workflow Wizard and Operations Center

**Status:** Implementation specification (not implemented)
**Base audited:** `main` @ `50198f70f2b93d29f147d618f93137d758bad477`, 25 October 2026
**Primary goal:** Replace scattered long-running operations with a reliable, inspectable, stepwise operator workflow while preserving all existing identity, provenance, INCI and catalog safety gates.

## 1. Observed baseline and critical gaps

- The admin UX is fragmented among `AdminObservatory.jsx` (~293 KB), `CatalogWorkflowsPanel.jsx` (~66 KB), `AggregationReviewPanel.jsx` (~59 KB), INCI/review modals, and queue components. Browser-local state controls too much of the navigation and perceived progress.
- `pipeline_tasks`, `pipeline_subtasks`, `pipeline_task_logs` and `scrape_checkpoints` already exist and MUST be reused or evolved, not replaced with another uncorrelated task history.
- `GET /tasks`, `GET /tasks/{id}`, `/tasks/{id}/logs` and `/tasks/{id}/subtasks` expose durable tasks; `GET /admin/workflows/products/discover-multiple/active` discovers running discovery workers.
- Discovery supports per-source/per-page progress, cooperative cancel, and manual next-page resumption, but lacks a first-class persisted parent workflow, exact repeatable resume action, worker leases and consistent paused state.
- `POST /admin/workflows/offers/refresh` is synchronous. Refactor to an asynchronous chunked operation before offering pause/resume.
- `WaterfallBatchRunner` in `api/routers/admin.py` and `SingleQueueRunner` in `api/pipeline/queue_runner.py` hold lifecycle/progress in process memory; a process restart currently loses this live state. Unify through a durable job runner/adapter while retaining the algorithms.
- Generic `cancel_task` sets `pipeline_tasks.status='cancelled'` immediately; this MUST become `cancel_requested` until cooperative worker termination is acknowledged, then `cancelled`. Show the operator that stopping does not undo already committed chunks.
- Existing identity matching, Gemini decisions, catalog canonical uniqueness, audited split/merge, INCI provenance and historical-ingredient exclusion are hard safety contracts. Do not weaken them for convenience.

## 2. Information architecture

Top-level admin routes (deep-linkable; browser Back supported):

1. **Operations Home** `/admin/operations`: active jobs and paused/interrupted jobs, saved workflow drafts, attention-needed tasks, catalog health. Single entry point.
2. **Workflows** `/admin/workflows`: Start or resume one of three guided workflows below. All steps visible in a left rail or top stepper with blockers and statuses.
3. **Job Center** `/admin/jobs`: ALL job types, with task/workflow/source/type/status filters; per-job timeline, logs, checkpoints, errors, throughput, API costs and actions. Backed by database records, not browser state.
4. **Review Workbench** `/admin/reviews`: separate **Product identity** and **INCI/formulation** queues; saved Gemini verdicts and human decisions have independent audit trails. Batch-select with previews.
5. **Catalog Explorer** `/admin/products`: searchable canonical products, retailer observations and offers, completeness, identity status, formulation versions, source evidence and provenance; easy drill-down from any step.
6. **Settings & Recovery** `/admin/maintenance`: source registry, credentials/provider health, quotas, consistent SQLite backups, selective reset/restore and diagnostics behind confirmations. No destructive resets inline in the ordinary wizard.

The default workflow is **Build / update catalog**:
- Step 1: **Select sources & scope** — saved configuration, enabled retailers, Shopify page bounds or unlimited scanning, optional source URLs, snapshot/fingerprint, expected scope.
- Step 2: **Discover and stage** — dry-run preview -> confirm -> start; per-source live cursor and source observations. Separate staged listing count vs matched existing vs provisional canonical count vs identity conflict count.
- Step 3: **Resolve identities** — deterministic safe rematch/verification -> preview/apply; saved Gemini identity verdicts -> preview/apply without new model calls; optional fresh Gemini execution in a cost-capped substep; human review of unresolved conflicts; never automatically merge conflicting variants. Show exclusions, unresolved, held and provisional separately. Allow continuation with verified subset while quarantining blocked products.
- Step 4: **Classify and validate** — taxonomy, skincare eligibility, duplicates and source/brand/variant data quality; preview and apply.
- Step 5: **Find full INCI** — eligible verified canonical products only; waterfall evidence tiers and authoritative provenance; preview/apply with per-canonical checkpoints. Candidate evidence is not publication. Historical exported INCI is not a trusted source.
- Step 6: **Resolve INCI review queues** — distinct Jev/Gemini INCI workers and human review; saved candidate decisions -> guarded apply, never use identity Gemini verdict to publish INCI. Show source, version, region, completeness and ordered ingredients.
- Step 7: **Quality gate & finalize** — cross-step reconciliation, coverage deltas, unresolved exception report, duplicate/uniqueness gates, evidence review, explicit publish/finish approval. Record manifest, dataset fingerprint, snapshots, counts and operator.

A second standalone **Refresh prices & stock** wizard uses source/scope -> preview -> background fetch/update existing offers ONLY -> reconcile. Never create products or alter identity/INCI. It must work without redoing discovery.

A third standalone **Repair existing catalog / INCI** wizard allows saved-source rematch, existing product ingredient remediation or QA on a bounded subset without rescraping, while retaining prerequisite validation, data lineage and independent review queues.

**Wizard navigation:** operator may save a draft and leave anytime; Next only becomes actionable after prerequisites for the selected population pass. Review-blocked items stay quarantined, not silently auto-approved. Allow stepping backward to inspect prior results, but modifying already-applied inputs creates a *new revision* and invalidates dependent previews, not prior executed audit history.

## 3. Global shell behavior

Header: catalog health; running/paused/interrupted job counts; global **Jobs** link always visible.
Side rail: named workflow and 7 step titles, each with `not_started | draft | ready | previewed | running | needs_review | completed | completed_with_errors | blocked` state and counts.
Main pane: **What this step does** / **Read and verify prerequisites** / **Configure** / **Preview** / **Apply or run** / **Results**. Avoid raw JSON as the default presentation.
Sticky action footer: `Save draft`, `Preview`, `Apply / Start` (disabled with explicit reason), `Pause` (when truly supported), `Stop after current unit`, `Continue` (gated).
Job drawer: task ID, workflow+step, source/product, current unit, processed, succeeded, failed, skipped, remaining if known, rolling throughput, timestamp of last heartbeat, last error, expandable logs, checkpoint and retry action.
A run is not considered complete on HTTP 202. Job statuses are backed by persisted terminal events.
For indefinite Shopify scans, NEVER display invented percent/ETA. Display source number, page number, products found, last checkpoint and animated indeterminate activity. For finite counted work show processed/total and measured rate.
Use clear nouns: **retailer listings**, **canonical products**, **offers**, **identity reviews**, **verified INCI**. Never mix their denominators.

## 4. Durable data model

Introduce additive schema migrations via the existing `init_db` pathway (no destructive edits of existing tables).

`admin_workflow_runs`
- `id TEXT PRIMARY KEY`, `kind`, `status`, `definition_version`, `revision INTEGER`, `created_by`, `created_at`, `updated_at`, `finished_at`, `current_step`, `configuration_json`, `dataset_fingerprint`, `parent_run_id`, `summary_json`.

`admin_workflow_steps`
- composite unique `(workflow_id, step_key, revision)`, `status`, `config_json`, `input_fingerprint`, `preview_json`, `preview_generated_at`, `approved_at`, `applied_at`, `result_json`, `blockers_json`, `job_id`.

`admin_job_controls` (could instead be additive columns to `pipeline_tasks`)
- `task_id`, `workflow_id`, `step_key`, `requested_action` (`none|pause|resume|stop`), `worker_state`, `worker_id`, `heartbeat_at`, `lease_expires_at`, `checkpoint_json`, `attempt`, `idempotency_key`, `started_by`, `last_error_json`. Index active states / lease expiry / workflow+step. Preserve `pipeline_tasks` as the canonical task counters/logs.
- `admin_workflow_events`: timestamped immutable lifecycle events, operator, action, task, before/after states, evidence fingerprint, counts and backup reference.

Add `workflow_id`/`step_key` to task metadata (or foreign key) for navigation. Every mutating operation requires an idempotency key, and compares the preview/input fingerprint against current source state and saved revision.

## 5. Job states and control guarantees

Job state machine:
`queued -> running -> succeeded | succeeded_with_errors | failed`
`running -> pause_requested -> paused -> resume_requested -> running`
`queued/running/paused -> stop_requested -> stopped`
`running/stop_requested/pause_requested -> interrupted` only after heartbeat lease expiry and process check, NEVER infer worker exit solely from a cancelled DB flag.
`interrupted/failed/stopped -> queued` on explicit **Retry / Resume from checkpoint** after reconciliation; increment attempt, retain prior history.

Control semantics:
- **Pause:** finish/rollback atomic current unit, persist checkpoint, acknowledge paused; must not silently continue into next source/product. Unsupported stages show **Pause unavailable** with explanation; do not render inert controls.
- **Stop:** graceful; preserve committed chunks and raw source observations, stop future work. Not rollback. UI uses **Stopping...** until worker exit ack.
- **Resume:** same workflow, same source run, saved cursor; reconcile the last committed unit and input fingerprint before processing. No restart from first page or repeated Gemini charge unless explicit operator choice.
- **Retry failed items:** retry only failed units with their failure reason and attempt count; ensure safe retries through `ON CONFLICT`, unique observation identity and transactional checkpoints.
- **Heartbeat:** worker writes per-stage liveness and leases; process restart detects expired leases and offers safe reconciliation/continuation. In-process background tasks are not adequate durable orchestration. Use a separate supervised worker process (single worker initially, SQLite WAL with transactions and lease-based claims); avoid unrelated Celery/Redis complexity.
- **Concurrency:** establish explicit locks for catalog rebuild, identity changes, formulation writes, and offer-refresh scope. Return a 409 that links the active blocking job. No duplicate same-type jobs from rapid double-click or browser retry.
- **Checkpoint unit:** Shopify one completed page per retailer; rematch one saved observation or bounded idempotent batch; identity Gemini a bounded group with persisted provider request/response and charge records; INCI one canonical product or bounded batch; prices existing offers by source+page; final publication by transactional manifest step. Commit the data changes and checkpoint atomically where feasible.
- **Progress:** `processed`, `succeeded`, `failed`, `skipped`, `held`, `queued`, `current_unit`, `last_event_at`. A nonzero failure count should produce an inspectable `completed_with_errors`, never generic success.

## 6. Backend API design (versioned adapter facade)

Use `/admin/v2` to avoid breaking existing routes while replacing the UI:
- `GET /dashboard` summary including ACTIVE jobs and orphan/interrupted workers.
- `GET /workflow-definitions`.
- `POST /workflows` create a saved draft; `GET /workflows`, `GET /workflows/{id}`, `PATCH /workflows/{id}` update draft revision; `GET /workflows/{id}/steps/{step}`.
- `PUT /workflows/{id}/steps/{step}/draft` save configuration; `POST /workflows/{id}/steps/{step}/preview` create signed/fingerprinted preview; `POST /workflows/{id}/steps/{step}/apply` enqueue idempotently using preview ID/fingerprint; `POST /workflows/{id}/steps/{step}/skip` only with explicit allowable reason.
- `GET /jobs?status=&workflow_id=&task_type=&source=&limit=&cursor=`; `GET /jobs/{id}`; `GET /jobs/{id}/events`; `GET /jobs/{id}/failures`; `GET /jobs/{id}/checkpoint`.
- `POST /jobs/{id}/pause|resume|stop|retry` — audited, idempotent commands whose response says **requested**, NOT falsely terminal.
- `GET /review-summary?kind=identity|inci` and the current specialized review endpoints behind typed adapters.
Initially poll `GET /jobs` and `GET /jobs/{id}` every 2–3s while active (with visibility/backoff handling); optional SSE can follow. Every page restores server state on load.

The orchestration facade calls existing policy-heavy services: discovery, rematch, guarded identity Gemini replay, taxonomy, INCI resolver, queues and quality audits. Do not reimplement matching/quality logic in React or an LLM. Convert existing synchronous/in-memory runners to chunked persistent workers before enabling controls.

## 7. Frontend component decomposition

Create `skinsync-app/src/admin-v2/`:
- `AdminShell.jsx`, `OperationsHome.jsx`, `WorkflowLibrary.jsx`, `WorkflowWizard.jsx`, `WizardStepRail.jsx`, `WizardActionBar.jsx`, `JobCenter.jsx`, `JobDetail.jsx`, `ReviewWorkbench.jsx`, `CatalogExplorer.jsx`, `MaintenanceCenter.jsx`.
- Steps under `steps/`: `SourceScopeStep`, `DiscoveryStep`, `IdentityStep`, `ClassificationStep`, `InciWaterfallStep`, `InciReviewStep`, `QualityPublishStep`, `OfferRefreshStep`.
- Reusable `JobProgress`, `CheckpointTimeline`, `PreviewDiff`, `ExceptionTable`, `EvidenceInspector`, `ConfirmApply`, `SourceProgress`, `BlockingJobNotice`.
- `api/adminV2.js` typed runtime-validated contract adapter, `hooks/useWorkflow.js`, `hooks/useJobs.js`, `workflowDefinitions.js`, `workflowTransitions.js`. Prefer testable state-driven components; no second 2,000-line monolith.
- Direct route links to a workflow ID and step and to specific task IDs. Back/forward navigation must not restart jobs.
- Responsive sidebar and accessible form labels; show error reason next to the action, explicit saved/previewed/applied states, a diff table of counts and side effects.
- Read-only feature-flagged V2 rollout before legacy screens are removed. Do not delete old admin routes until jobs and history parity are proven.

## 8. Safety and approvals

- `Save draft`: no data writes outside workflow control state.
- `Preview`: no catalog writes, no Gemini charge, reports denominator and fingerprint, expected mutations/review holds, active blockers and backup requirement.
- `Apply`: confirm exact preview/revision and independent guard checks; stale snapshots get `409 stale_preview`, forcing a fresh preview.
- Every action writes an immutable audit event including operator, dataset fingerprint, version, item counts, and side-effect category.
- Preview and apply saved identity Gemini verdicts without provider calls. Keep identity Gemini and INCI Jev/Gemini physically and semantically separate; never apply an identity verdict as full INCI verification.
- Respect canonical uniqueness, source observations and immutable audit history. New unsupported source listings remain provisional; no fabricated canonical anchor; split by defensible formulation groups, not one canonical per retailer listing.
- Historical ingredient CSVs and title-only ingredient matches remain untrusted. INCI publication requires source, exact formulation/variant/region/version, complete ordered list, and evidence pass.
- Preserve source data/backup while retrying; destructive reset/restore require fresh preflight, consistent WAL backup, no live writers, explicit typed confirmation and audited result. No irreversible maintenance action in normal wizard.
- If a job affects only a verified subset, show both progressed and quarantined counts; no false zero-backlog message when 500 review cases remain.

## 9. Rollout order (no big bang)

**Phase A — backend foundation:** migration + workflow/step records + job adapter facade + common status machine + lease/heartbeat + idempotency + read-only job center. Validate on existing tasks, handle legacy tasks explicitly.

**Phase B — live Operations Home and Job Center:** show ALL running discovery/identity/rematch/INCI/queue work; recover after browser reload; logs and statuses; detect stale worker, exact blocker links.

**Phase C — discovery/identity wizard:** saved configs, fingerprinted previews, apply, durable source checkpoint resume, deterministic rematch, identity review / saved Gemini replay, QA. Implement pause/stop/retry; ensure no duplicate canonical.

**Phase D — INCI wizard:** persistent waterfall runner, canonical-level checkpoints; candidate vs verified counts; separate INCI LLM queue with budget and controls; QA/publish.

**Phase E — offers and maintenance:** move offer refresh to async bounded chunks; connect recover/backup/restore through a protected Maintenance Center.

**Phase F — cutover:** regression/test suite, old-new count parity, remove obsolete modal entry points, split AdminObservatory progressively, update runbooks and API/database docs.

## 10. Non-negotiable acceptance tests

| Test | Pass condition |
|---|---|
| Browser reload during page 15 of a Shopify scan | Job reappears from DB with same task ID, source, page, counts and control state; no implicit restart |
| Backend process killed during a chunk | Task transitions to interrupted after expired lease; stored checkpoint and committed records intact; reconciling resume does not duplicate observations |
| Pause requested mid-source | UI shows pause_requested until current page commits/rolls back and worker acknowledges paused; no next page is fetched |
| Stop requested mid-Gemini call | Stop after current in-flight batch and persist its response; no duplicate billable request on retry |
| Retry 617 failed rematches | Explicitly retry failed items only; show per-reason counts; preserve successful rematches and reviews |
| Save source/INCI settings then leave | Reopen saved workflow with same config, revisions, previews and results |
| Preview stale after catalog changes | Apply blocked 409; no catalog mutation; instruct refresh preview |
| Concurrent conflicting mutation | Block with link to active owner job; do not start hidden duplicate task |
| Unknown discovery size | Display per-source page/current item and indeterminate status, never fake percentage |
| Offer-only refresh | Never imports canonicals/offers or changes INCI/identity |
| Identity Gemini verdict replay | Zero provider calls; safe verified/split/held counts and full audit |
| INCI historical/title-only candidate | Remains evidence-only/unverified; no publication |
| Quality gate | Detect duplicate canonical identity, conflicting formula, missing provenance, unresolved holds and step reconciliation |
| Completed with item failures | Correctly says completed_with_errors and provides downloadable/inspectable failures |
| Recovery + maintenance | Prevent restore/reset with a live writer, require backup/preview/reconfirmation, preserve audit |
| Existing tests | All API/admin/aggregation regression tests pass; Vite build and lint pass |

## 11. Definition of done

The old screens may be retired only when the operator can start a saved workflow, leave the browser, locate every live/paused/interrupted job across worker types, inspect progress and last checkpoint, pause/stop/resume supported long operations, retry failed units without redoing successful work, explicitly preview and apply each stage, understand quarantined exceptions, audit exact catalog changes and finish a verified INCI-aware build with no loss of existing safety constraints.

**Do not claim completion merely because a wizard stepper and buttons render.** Backend controls and persistence must be tested under real interruption and restart.


## 12. AI & API Operations — first-class administration (revision 26 October 2026)

The initial UX proposal underrepresented existing provider operations. **AI & API Operations** is now a dedicated top-level section, separate from Job Center, Review Workbench and Maintenance, but interconnected with the wizard at Identity (step 3), INCI research (step 5) and INCI queues (step 6).

### Four screens

1. **Providers & health:** Jev 1.13, Gemini, LangSearch and discovered research/MCP adapters; configured health, stale telemetry warnings, model, rate limit, daily quota and next reset. Gemini identity and INCI share a provider budget, not approval rules. Provide direct links to queue and job details. Display secret presence/status only, never raw keys. The existing `GET /admin/apis/status` and `GET /admin/langsearch/quota-status` are starting read adapters, not sufficient live job state.
2. **Queues:** Product identity Gemini, INCI Jev, INCI Gemini, ingredient research/ LangSearch request backlogs, plus human identity and human INCI review (the latter are NOT runnable API workers). Queues show eligible/pending/processing/held/error counts, queued-since age, queue selection and independent pause/resume/stop semantics. The existing `/admin/queues/*` manages Jev/INCI Gemini; `/admin/aggregation/reviews/gemini/*` independently manages identity Gemini. Preserve exact separation. Reviews are handled in Review Workbench; a model verdict cannot automatically approve identity or publish ingredients.
3. **Batch manager:** Select exact queue and scope; use saved-queue fingerprint + eligibility count and active blockers; set max items, per-call batch size when provider supports it, max requests, quota/cost ceiling, concurrency/backoff and dry-run. Preview must distinguish zero-charge saved-response replay from fresh billable calls. An approved batch creates a durable worker job; its checkpoint, request/response audit and per-item failure/retry policy are visible in Job Center. Stop/pause must acknowledge worker exit/unit boundary and persist the last request response to avoid replay billing.
4. **Settings & limits:** Read-only effective runtime config vs audited operator overrides, with provider-side hard limits always distinguished from local safety caps; model choices, identity threshold, INCI confidence/quality policy, batch sizes, rate limits, timeouts, backoff, provider handoff, enable/disable, credential status and quota reset workflow. Save config revisions; running jobs retain the exact settings snapshot with which they started. Do not imply an admin button actually changes `.env` or a provider's external quota unless wired and verified.

### Actual baseline settings from current `pipeline/config.py`

- Gemini: `GEMINI_MODEL_NAME` default `gemini-3.5-flash-lite`, `GEMINI_MAX_RPM=15`, `GEMINI_MAX_RPD=1500`, `GEMINI_BATCH_SIZE=10`, `GEMINI_TIMEOUT_SECONDS=35`.
- Jev: `JEV_MODEL_NAME` default `jev-1.13-free`, `JEV_REQUEST_INTERVAL_SECONDS=2`, `JEV_RATE_LIMIT_BACKOFF_SECONDS=10`, `JEV_TIMEOUT_SECONDS=8`.
- LangSearch: `LANGSEARCH_DAILY_TOKEN_LIMIT=10000000`, `LANGSEARCH_MAX_RPS=5`, `LANGSEARCH_ROLLING_TPM_LIMIT=1000000`, `LANGSEARCH_TIMEOUT_SECONDS=15`. Code exposes manual quota-exhaust and reset; these are *local tracking controls*, not evidence that the provider increased its allowance.
- Guarded saved identity-Gemini auto-apply is conventionally reviewed at confidence `>=0.90` and **never** bypasses deterministic brand / variant / current-evidence checks. Keep identity and INCI thresholds independent.

### Navigation and contract

`Admin → AI & API Operations → [Providers & health | Queues | Batch manager | Settings & limits]`.

Proposed `/admin/v2/providers/*`, `/admin/v2/queues/*`, `/admin/v2/api-batches/*`, `/admin/v2/provider-settings/*` facade routes link to the existing routers and durable job layer. Provider requests, queue items, individual batch jobs and completed/applied decisions are distinct entities. Batch starting is never identical to batch completion. All changes (especially quota resets and policy updates) require explicit audit and a revisioned configuration snapshot.

### UX completion tests

- See both Gemini identity and Gemini INCI queue depths but a single accurate shared provider quota.
- Run / pause / resume a bounded Jev or Gemini batch, navigate elsewhere, and restore status without restarting or duplicate API calls.
- Search LangSearch request jobs and source evidence without presenting search snippets as authoritative full INCI.
- Preview and apply saved Gemini verdicts with zero external calls; keep rejected or stale decisions held for human review.
- Inspect configured limits and show exact effective settings used per historical job; do not reveal API keys.
- Quota-exhaust/reset requires explicit reason and audit; provider-side usage must never be inferred solely from local counter reset.
- Link a wizard step to its specific queue and originating batch/job, not a second independent execution record.

**Prototype:** `skinsync_admin_v2_1_ai_operations_wireframes.html` (conversation attachment, illustrative and not connected to the backend).
