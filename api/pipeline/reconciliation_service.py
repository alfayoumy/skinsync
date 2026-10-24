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
