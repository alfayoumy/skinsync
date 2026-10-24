"""Dry-run reconciliation against a disposable catalog; live IDs remain untouched."""
import sqlite3
import tempfile
from pathlib import Path
from pipeline.db_manager import init_db,get_connection


def reconcile_catalog(conn):
    if conn.in_transaction:raise ValueError("Commit or roll back pending changes before taking a reconciliation snapshot.")
    from pipeline.product_linker import link_catalog_products
    with tempfile.TemporaryDirectory(prefix='skinsync-reconcile-') as directory:
        path=Path(directory)/'catalog.db'
        copy=sqlite3.connect(path)
        try:conn.backup(copy)
        finally:copy.close()
        proposed=get_connection(str(path))
        try:
            init_db(db_path=str(path),conn=proposed,seed_defaults=False)
            before={r[0]:r[1] for r in proposed.execute('SELECT id,canonical_product_id FROM products')}
            metrics=link_catalog_products(conn=proposed)
            changes=[{'product_id':row[0],'before':before.get(row[0]),'proposed':row[1]}
                     for row in proposed.execute('SELECT id,canonical_product_id FROM products') if row[1]!=before.get(row[0])]
            reviews=[dict(r) for r in proposed.execute("SELECT * FROM product_review_queue WHERE status='pending'")]
            return {'dry_run':True,'metrics':metrics,'identity_changes':changes,'reviews':reviews,'live_database_writes':0}
        finally:proposed.close()


def live_fingerprint(conn):
    import hashlib,json
    # The preview depends on links, constraints, reviewer decisions and evidence,
    # not just the two public catalog tables. Any mutation invalidates approval.
    relevant=('products','canonical_products','product_identity_links',
              'canonical_product_aliases','canonical_identity_constraints',
              'canonical_identity_reviews','product_review_queue',
              'product_offers','inci_audit_evidence','brands','brand_aliases',
              'product_taxonomy','sources')
    known={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    payload={table:[dict(r) for r in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')]
             for table in relevant if table in known}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def save_reconciliation_preview(conn,reviewer):
    import json
    baseline=live_fingerprint(conn)
    report=reconcile_catalog(conn)
    if live_fingerprint(conn)!=baseline:raise ValueError('Catalog changed during preview generation.')
    cursor=conn.execute('INSERT INTO reconciliation_previews(baseline_hash,report,created_by) VALUES (?,?,?)',
                        (baseline,json.dumps(report,sort_keys=True),reviewer))
    return {'preview_id':cursor.lastrowid,'baseline_hash':baseline,'review_count':len(report['reviews'])}


def persist_reconciliation_findings(conn,preview_id,reviewer,reason):
    import json
    from pipeline.review_service import require_review
    from pipeline.aggregation_service import queue_review
    require_review(reviewer,reason)
    row=conn.execute('SELECT * FROM reconciliation_previews WHERE id=?',(preview_id,)).fetchone()
    if not row:raise ValueError('Preview not found.')
    if live_fingerprint(conn)!=row['baseline_hash']:raise ValueError('Catalog changed since preview; generate a new snapshot.')
    report=json.loads(row['report']);ids=[]
    for finding in report['reviews']:
        details=json.loads(finding['details'])
        details['approved_snapshot']={'preview_id':preview_id,'baseline_hash':row['baseline_hash'],'approved_by':reviewer,'reason':reason}
        ids.append(queue_review(conn,finding['kind'],finding['source_id'],finding['source_product_id'],finding['canonical_product_id'],details))
    # Attach status only; never rewrite or delete historical ingredient declarations.
    conn.execute("""UPDATE canonical_products SET formulation_provenance='legacy_unverified'
        WHERE ingredients_raw IS NOT NULL AND TRIM(ingredients_raw)!='' AND NOT EXISTS
        (SELECT 1 FROM inci_audit_evidence e WHERE e.canonical_product_id=canonical_products.id AND e.decision='approved_authoritative')""")
    return {'preview_id':preview_id,'persisted_review_ids':ids,'identity_reassignments':0,'formula_changes':0}
