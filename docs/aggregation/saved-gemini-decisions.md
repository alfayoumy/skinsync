# Apply saved Gemini identity decisions

Product Matching Reviews now provides Preview saved decisions and Apply confident decisions — no Gemini calls.

The worker replays only previously evaluated, still pending cases that have a structured saved Gemini verdict and at least 90 percent confidence. Preview uses reversible SQLite savepoints and never changes the catalog.

Applying performs deterministic identity checks, saves compatible same-product links, verifies their canonical IDs, and closes completed reviews. High-confidence different-product decisions trigger an audited canonical split only when explicit product markers permit a safe retailer partition anchored to the retained canonical (for example NUXE Prodigieuse original, Florale and Golden, or Acti-Clear versus Acti-Repair). Newly split canonical products remain provisional and retain no automatically inherited INCI. Cannot-link constraints and audit history are recorded.

Cases without sufficient partition evidence, with conflicting formulas or brands, or with stale source evidence remain in manual review. Gemini confidence does not override these checks. This action never reissues a provider call and never merges two canonical IDs.

The UI provides counts of stored cases examined, projected identity verifications, projected splits and cases remaining for review. Applying runs in a tracked task with 25-case checkpoints and a stop control. Completed cases cannot be applied twice.
