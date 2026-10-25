"""Read-only catalog completeness, identity, taxonomy and INCI risk report.

Counts are *audit flags*, not proof of duplication or formula invalidity.
Preserve the larger restored catalog; do not silently rewrite any records.
"""
import sqlite3
from pipeline.catalog_restore import list_catalog_backups, _database_path, _chosen_backup


def _count(conn, sql, params=()):
    return conn.execute(sql,params).fetchone()[0]


def _count_snapshot(conn):
    names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts={}
    for t in ('products','canonical_products','source_product_observations','product_offers',
              'inci_audit_evidence','product_review_queue','identity_gemini_queue'):
        if t in names:
            counts[t]=_count(conn,'SELECT COUNT(*) FROM "'+t+'"')
    if 'source_product_observations' in names:
        counts['unique_source_products']=_count(conn,"""SELECT COUNT(*) FROM (
            SELECT 1 FROM source_product_observations
            GROUP BY source_id,source_product_id)""")
    return counts


def inspect_catalog_quality(conn,db_path=None):
    path=_database_path(conn,db_path)
    backups=list_catalog_backups(conn,str(path))
    pending=backups.get('pending')
    last=backups.get('last_restore')
    names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts=_count_snapshot(conn)
    # Readable, disk-backed restoration state, not an inferred UI status.
    history=None
    if 'catalog_restore_history' in names:
        row=conn.execute("""SELECT restored_at,actor,source_backup,rescue_backup,source_sha256
            FROM catalog_restore_history ORDER BY id DESC LIMIT 1""").fetchone()
        if row:
            history={'restored_at':row[0],'actor':row[1],'filename':row[2],
                     'rescue_backup':row[3],'sha256':row[4]}
    verified_restore=bool(last and history and last.get('filename')==history['filename']
                          and not pending)
    stage='restart_required' if pending else ('restored' if verified_restore
             else ('restore_record_unconfirmed' if last else 'no_recent_restore'))
    snapshot_counts=None
    baseline_delta=None
    if verified_restore:
        try:
            source=_chosen_backup(path,history['filename'])
            with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as backup:
                snapshot_counts=_count_snapshot(backup)
            baseline_delta={t:counts.get(t,0)-value for t,value in snapshot_counts.items()}
        except (ValueError,sqlite3.Error,OSError):
            # Historical file may have moved after successful restoration.
            pass

    integrity=conn.execute('PRAGMA quick_check').fetchone()[0]
    fk_count=sum(1 for _ in conn.execute('PRAGMA foreign_key_check'))
    identities={}
    categories={}
    pending_reviews={}
    if 'canonical_products' in names:
        identities={str(r[0] or 'unknown'):r[1] for r in conn.execute(
            "SELECT identity_status,COUNT(*) FROM canonical_products GROUP BY identity_status")}
        categories={str(r[0] or 'unknown'):r[1] for r in conn.execute(
            "SELECT classification_status,COUNT(*) FROM canonical_products GROUP BY classification_status")}
    if 'product_review_queue' in names:
        pending_reviews={str(r[0]):r[1] for r in conn.execute(
            "SELECT kind,COUNT(*) FROM product_review_queue WHERE status='pending' GROUP BY kind")}
    indicators={}
    if 'canonical_products' in names:
        indicators['approved_formula_on_unverified_identity']=_count(conn,"""SELECT COUNT(*)
            FROM canonical_products WHERE COALESCE(TRIM(ingredients_raw),'')!=''
            AND COALESCE(identity_status,'')!='verified'""")
        indicators['historical_scrape_formula_still_assigned']=_count(conn,"""SELECT COUNT(*)
            FROM canonical_products WHERE COALESCE(TRIM(ingredients_raw),'')!=''
            AND ingredient_source='historical_store_scrape'""")
        indicators['verified_skincare_without_inci']=_count(conn,"""SELECT COUNT(*)
            FROM canonical_products WHERE is_skincare=1 AND identity_status='verified'
            AND COALESCE(TRIM(ingredients_raw),'')=''""")
        indicators['unverified_skincare_identities']=_count(conn,"""SELECT COUNT(*)
            FROM canonical_products WHERE is_skincare=1 AND COALESCE(identity_status,'')!='verified'""")
        indicators['category_pending_or_legacy']=_count(conn,"""SELECT COUNT(*)
            FROM canonical_products WHERE COALESCE(classification_status,'')
            NOT IN ('approved','auto_approved')""")
        indicators['exact_brand_title_collision_groups']=_count(conn,"""SELECT COUNT(*) FROM (
            SELECT brand,canonical_title FROM canonical_products
            WHERE COALESCE(brand,'')!='' AND COALESCE(canonical_title,'')!=''
            GROUP BY lower(trim(brand)),lower(trim(canonical_title))
            HAVING COUNT(*)>1)""")
    if 'products' in names:
        indicators['legacy_listings_without_canonical']=_count(conn,
            'SELECT COUNT(*) FROM products WHERE canonical_product_id IS NULL')
    if 'canonical_product_aliases' in names:
        indicators['verified_gtin_shared_by_multiple_canonicals']=_count(conn,"""SELECT COUNT(*) FROM (
            SELECT alias_value FROM canonical_product_aliases
            WHERE alias_type='gtin' AND verified=1 AND alias_value!=''
            GROUP BY alias_value HAVING COUNT(DISTINCT canonical_product_id)>1)""")
    if 'inci_audit_evidence' in names and 'canonical_products' in names:
        indicators['approved_formula_missing_approval_evidence']=_count(conn,"""SELECT COUNT(*)
            FROM canonical_products c
            WHERE COALESCE(TRIM(c.ingredients_raw),'')!=''
            AND NOT EXISTS (SELECT 1 FROM inci_audit_evidence e
                WHERE e.canonical_product_id=c.id AND e.decision='approved_authoritative')""")
        indicators['multiple_full_ordered_source_declarations']=_count(conn,"""SELECT COUNT(*) FROM (
            SELECT canonical_product_id FROM inci_audit_evidence
            WHERE completeness='full' AND order_verified=1
            AND source_name LIKE 'source:%'
            GROUP BY canonical_product_id HAVING COUNT(DISTINCT parsed_tokens)>1)""") if 'order_verified' in {r[1] for r in conn.execute("PRAGMA table_info(inci_audit_evidence)")} else 0

    by_source=[]
    if 'sources' in names:
        for row in conn.execute("""SELECT id,name FROM sources
            WHERE source_type='retailer' ORDER BY wave,id"""):
            sid,name=row
            listings=_count(conn,'SELECT COUNT(*) FROM products WHERE store_id=?',(sid,)) if 'products' in names else 0
            obs=_count(conn,"""SELECT COUNT(*) FROM (
                SELECT 1 FROM source_product_observations WHERE source_id=?
                GROUP BY source_product_id)""",(sid,)) if 'source_product_observations' in names else 0
            offers=_count(conn,'SELECT COUNT(*) FROM product_offers WHERE source_id=?',(sid,)) if 'product_offers' in names else 0
            by_source.append({'id':sid,'name':name,'historical_listings':listings,
                              'observed_unique_products':obs,'variant_offers':offers})
    return {'restore':{'status':stage,'verified_completed':verified_restore,
                       'pending_filename':pending.get('filename') if pending else None,
                       'last_restore':history or last,
                       'baseline_counts':snapshot_counts,'current_minus_baseline':baseline_delta},
            'integrity':{'quick_check':integrity,'foreign_key_errors':fk_count},
            'catalog_counts':counts,'identity_statuses':identities,
            'classification_statuses':categories,'pending_reviews':pending_reviews,
            'risk_indicators':indicators,'sources':by_source,
            'interpretation':{
                'restored':'Confirmed by backend restore manifest and database audit record. Counts can differ from the saved baseline after subsequent catalog updates.',
                'duplicates':'Exact brand/title collisions and shared verified GTIN are review candidates, never automatically duplicate identities.',
                'inci':'Historical and already approved formulas are not inherently verified by database restore. Revalidate canonical identity and original INCI source before any replacement.',
                'completeness':'A new storefront scan may yield fewer products than historical records. Do not delete historical products because they are absent from a new scan.'}}
