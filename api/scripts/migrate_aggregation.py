"""Snapshot the catalog before additive aggregation migration. Defaults to a dry run."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import DB_PATH,init_db,get_connection


def migrate(database,backup,apply=False):
    database=Path(database).resolve();backup=Path(backup).resolve()
    if database==backup or backup.exists():raise ValueError('Choose a new backup file distinct from the source catalog.')
    source=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    snapshot=sqlite3.connect(backup)
    try:source.backup(snapshot)
    finally:source.close();snapshot.close()
    with tempfile.TemporaryDirectory(prefix='skinsync-migration-') as directory:
        target=database if apply else Path(directory)/'dry-run.db'
        if not apply:
            source=sqlite3.connect(backup);destination=sqlite3.connect(target)
            try:source.backup(destination)
            finally:source.close();destination.close()
        conn=get_connection(str(target))
        try:
            before_mapping={r[0]:r[1] for r in conn.execute('SELECT id,canonical_product_id FROM products')}
            before=len(before_mapping)
            init_db(db_path=str(target),conn=conn,seed_defaults=False)
            mapped=conn.execute('SELECT COUNT(*) FROM legacy_identity_mapping').fetchone()[0]
            invalid=sum(before_mapping.get(row[0])!=row[1] for row in conn.execute('SELECT id,canonical_product_id FROM products'))
            if invalid or mapped<before:raise ValueError('Migration changed a legacy identity mapping.')
            return {'applied':apply,'backup':str(backup),'legacy_products':before,'preserved_mappings':mapped,
                    'sources_registered':conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]}
        finally:conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',default=DB_PATH)
    parser.add_argument('--backup',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(migrate(args.database,args.backup,args.apply),indent=2))
