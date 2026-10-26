"""Live catalog coverage and pipeline blockers, without mutation or provider calls."""
def catalog_health(conn):
    def count(sql):
        return conn.execute(sql).fetchone()[0]
    reviews={r[0]:r[1] for r in conn.execute("SELECT kind,COUNT(*) FROM product_review_queue WHERE status='pending' GROUP BY kind")}
    return {
        'canonical_products':count('SELECT COUNT(*) FROM canonical_products'),
        'active_canonical_products':count("SELECT COUNT(*) FROM canonical_products WHERE identity_status NOT IN ('redirected','reversed_split')"),
        'active_skincare_products':count("SELECT COUNT(*) FROM canonical_products WHERE is_skincare=1 AND identity_status NOT IN ('redirected','reversed_split')"),
        'verified_identities':count("SELECT COUNT(*) FROM canonical_products WHERE identity_status='verified'"),
        'published_inci':count("SELECT COUNT(*) FROM canonical_products WHERE COALESCE(TRIM(ingredients_raw),'')!='' AND identity_status NOT IN ('redirected','reversed_split')"),
        'published_skincare_inci':count("SELECT COUNT(*) FROM canonical_products WHERE is_skincare=1 AND COALESCE(TRIM(ingredients_raw),'')!='' AND identity_status NOT IN ('redirected','reversed_split')"),
        'observed_products_with_inci':count("SELECT COUNT(*) FROM (SELECT source_id,source_product_id FROM source_product_observations WHERE COALESCE(TRIM(ingredient_text),'')!='' GROUP BY source_id,source_product_id)"),
        'pending_reviews':reviews,
        'pending_identity_reviews':sum(reviews.get(k,0) for k in ('identity','duplicate')),
        'pending_category_reviews':reviews.get('category',0),
        'awaiting_evidence':count("SELECT COUNT(*) FROM product_review_queue WHERE status='deferred'"),
        'sources':[dict(r) for r in conn.execute("SELECT id,name,enabled,publish_enabled FROM sources WHERE enabled=1 ORDER BY id")],
        'formula_evidence':[dict(r) for r in conn.execute("SELECT completeness,order_verified,COUNT(*) count FROM inci_audit_evidence WHERE decision='candidate_evidence' GROUP BY completeness,order_verified")],
        'gemini_results':[dict(r) for r in conn.execute("SELECT status,verdict,COUNT(*) count FROM identity_gemini_queue GROUP BY status,verdict")],
    }
