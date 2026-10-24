# Gemini-assisted product identity reviews

Use **Admin → Product matching reviews** for product identity discrepancies. This is separate from the Gemini INCI queue and has no permission to approve or overwrite ingredients.

1. Click **Find legacy products needing review** to persist cases from the current legacy catalog.
2. Click **Send all unresolved identity cases to Gemini** (or select individual cases).
3. Select **cases per Gemini call** (5, 10, or 20) and **cases per run** (default 500).
4. Start the Gemini worker; the UI shows progress, automatic resolutions, requests made and remaining manual cases.
5. Open **Needs manual review** to see uncertain, conflicting, or missing-data cases. Records queued or being processed are shown separately under **Waiting for Gemini**.

The worker uses the installed Gemini API key/model and the same shared request limiter as the existing INCI worker. Each Gemini request receives multiple *cases*, each containing the canonical title and associated retailer names/titles. Returned structured verdicts are checked against the original case ID. Invalid or missing results never count as approvals. Failed requests retain queued cases for retry; interrupted runs recover orphaned processing cases after restart.

## Automatic-resolution policy

A Gemini verdict alone is never sufficient. Auto resolution requires:

- Verdict **same_product** and confidence of at least 0.97
- One canonical product only (never automatic merge of two canonical IDs)
- Consistent approved/curated brand aliases; for example, Avène and Eau Thermale Avène
- At least two independent retailer sources corroborating the same cluster
- The same retailer listings and canonical title as the reviewed snapshot
- No deterministic hard conflicts (SPF, active concentration, scent, tint, formulation vehicle, etc.)
- No other unresolved identity cases affecting the canonical record
- No competing duplicate canonical identity
- No conflicting credible full INCI declarations

For an approved case the worker writes validated source-to-canonical links, marks the canonical identity verified, closes the review case and saves full decision history and model reasoning. It never writes ingredient lists, changes prices, or merges canonicals. Different formulations, incomplete evidence, or uncertain model decisions remain in Product Reviews.

## Operational verification

Pull latest main, restart the backend so the additive queue table is created, and reload the frontend. Confirm GEMINI_API_KEY and GEMINI_MODEL_NAME are configured. First queue a small sample and run 5–10 cases per call; inspect the model reasons and resulting audited cases before processing thousands.

Do not claim a Gemini run has occurred just because code or tests have been committed: production database and provider credentials remain on the deployment host.
