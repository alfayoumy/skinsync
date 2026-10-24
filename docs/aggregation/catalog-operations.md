# Catalog operations — distinct admin workflows

## Product sources versus Product Reviews

**Manage product sources:** collection enable/disable, connection diagnostics, source coverage. This is a separate screen from product identity decisions.

**Product matching reviews:** persisted ambiguous identities, potential duplicate canonicals, conflicting formula evidence, and classification questions. Review only shows saved cases, not read-only preview diagnostics.

From Product Reviews, select **Find legacy products needing review**. This creates up to 250 review cases without certifying any product. Repeating it advances through remaining eligible conflicts without duplicating already pending cases. The Add & match products workflow also has **Prepare review cases & open Product Reviews**.

## Import from many sources

Use **Catalog operations → Add & match products**, enable multi-source mode and select all enabled collection sources or any subset.

Shopify sources scan the requested catalog pages. Other sources collect supplied product URLs (entered as source_id | URL) or URLs already observed. Sources without known URLs or a supported catalog feed are explicitly marked needs_urls; they are NOT reported as successfully crawled. One source's error does not stop the rest. The tracked job reports findings per source.

## Automatic verification

**Find safe exact matches** scans ALL remaining legacy skincare identities and reports total safe candidates. **Apply next 500 safe matches** changes only a bounded number of identity records, reports the actual number saved and the number remaining, and creates pending review cases for conflicts. Repeating it progresses to the next records rather than repeating the first 500. It does not modify ingredient formulas or retail prices.

A product with incompatible variants, conflicting credible INCI, unresolved brands or a duplicate canonical identity is not auto-verified.

## Deployment checklist

Pull latest main, restart the API and rebuild/reload the frontend. Preview legacy identity matches, apply one safe batch and verify the saved count and smaller remaining count. Open Product Reviews to see the persisted cases. Run a multi-source dry run and inspect the outcome for each selected source. GitHub commits and CI do not mutate the running local product database.


## Shopify catalog pages

The former 1–3 page testing limit was not a Shopify limit. Under **Add & match products**, choose **All pages** to continue from the starting page until an empty Shopify catalog page is returned. Alternatively disable **All pages** and specify any positive number of pages. Single-store discovery also uses a tracked background job.

For large jobs, the UI shows the current source/page and permits **Stop scan after the current page**. Completed pages of a real import are committed separately; the response includes the last completed page and the next page for manual resumption. Dry-run records are rolled back immediately per observation. Source-specific rate limiting and retry/backoff are preserved. HTTP errors or repeated identical pages are NOT classified as successful end-of-catalog completion.

**Update prices & stock** remains a separate offer-only operation. Its manual page-count field no longer has a three-page validation cap, but that synchronous endpoint does not yet implement indefinite background crawling. For a full initial product catalog, use **Add & match products → All pages**.
