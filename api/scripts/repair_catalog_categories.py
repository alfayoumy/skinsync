"""Repair category evidence only; safe alongside independent pair-comparison work."""
import argparse
import json
import sys
import sqlite3
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.catalog_reset import _backup,guard_catalog_writes
from pipeline.catalog_repair import repair_categories,repair_canonical_categories,repair_explicit_hair_types


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args();conn=get_connection();preview_folder=None
    try:
        guard_catalog_writes(conn)
        if not args.apply:
            preview_folder=tempfile.TemporaryDirectory(prefix='catalog-category-preview-')
            snapshot=sqlite3.connect(str(Path(preview_folder.name)/'preview.sqlite3'))
            snapshot.row_factory=sqlite3.Row
            conn.backup(snapshot);conn.close();conn=snapshot
        backup=str(_backup(conn,Path(conn.execute('PRAGMA database_list').fetchone()[2]))) if args.apply else None
        conn.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
        before=conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE kind='category' AND status='pending'").fetchone()[0]
        hair=repair_explicit_hair_types(conn)
        result=repair_categories(conn)
        projection=repair_canonical_categories(conn)
        after=conn.execute("SELECT COUNT(*) FROM product_review_queue WHERE kind='category' AND status='pending'").fetchone()[0]
        if args.apply:conn.commit()
        else:conn.rollback()
        report={'applied':args.apply,'backup_path':backup,'preview_scope':None if args.apply else 'consistent_isolated_snapshot','before':before,'after':after,'result':result,
                'canonical_projection':projection,'explicit_hair_types':hair}
        value=json.dumps(report,indent=2)
        if args.output:args.output.write_text(value+'\n')
        print(value)
    finally:
        conn.close()
        if preview_folder:preview_folder.cleanup()


if __name__=='__main__':main()
