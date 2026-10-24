# SkinSync frontend

React 19 + Vite 7 frontend for the SkinSync product catalog, recommendation interface and administrator observatory.

## Local development

Requires Node.js 22/npm (matching GitHub Actions). The API should be started separately from `api/app.py` at `http://127.0.0.1:8000`.

```bash
cd skinsync-app
npm ci
npm run dev
npm run build
npm run lint
```

Development origin defaults to `http://127.0.0.1:5173`. The API permits known origins through `SKINSYNC_ALLOWED_ORIGINS` (do not use a wildcard). Admin requests use `src/apiFetch.js`, which attaches an administrator Bearer token to requests sent to `localhost:8000` or `127.0.0.1:8000`. For remote hosts the API URL and credential forwarding must be explicitly configured; this is not a generic production-host selector.

## Admin screens

- **Catalog operations → Add & match products:** source selection, tracked multi-source discovery, Shopify All pages, dry run, classification/identity linking, safe legacy auto-verification and review preparation.
- **Catalog operations → Update prices & stock:** existing linked retailer offers only; finite page count and dry-run first.
- **Catalog operations → Find ingredients:** identity-approved existing skincare products, INCI waterfall, evidence-only versus published formulations.
- **Catalog operations → AI review queues:** separate Jev/Gemini **INCI** queues; Gemini **product identity** actions appear in Product Matching Reviews.
- **Manage product sources:** registered source settings, coverage and connection tests, independent of review decisions.
- **Product Matching Reviews:** pending identity/duplicate/variant cases, batch Gemini identity enqueue/run, saved-decision preview/apply (no Gemini calls), manual actions, and the sortable/paginated **Gemini Decisions** history table.
- **Gemini Decisions table:** filters by verdict, application outcome, review status and confidence range; search by product/ID; sortable columns; expandable original retailer evidence and separate model/application explanations.

The UI uses the current FastAPI backend and does not itself guarantee that a remote provider request or asynchronous job succeeded. Always inspect the job status and stored outcome.

## Engineering references

- [Architecture](../docs/system_architecture_and_api_documentation.md)
- [HTTP API reference](../docs/api-reference.md)
- [Operations and security](../docs/workflows-and-operations.md)
- [Database](../docs/database-reference.md)

Frontend source: `src/AdminObservatory.jsx`, `src/CatalogWorkflowsPanel.jsx`, `src/AggregationReviewPanel.jsx`, `src/GeminiDecisionsTable.jsx` and `src/apiFetch.js`.
