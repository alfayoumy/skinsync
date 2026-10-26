"""Preview by default; --apply backs up SQLite before repairing retained evidence."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.catalog_repair import repair_catalog
from pipeline.catalog_health import catalog_health
from pipeline.catalog_reset import _backup


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--approve-staged',action='store_true',help='Individually approve compatible full ordered EG retailer INCI on verified identities; source policies remain unchanged.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    conn=get_connection(args.db) if args.db else get_connection()
    try:
        path=Path(conn.execute('PRAGMA database_list').fetchone()[2])
        before=catalog_health(conn)
        backup=str(_backup(conn,path)) if args.apply else None
        conn.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
        result=repair_catalog(conn,dry_run=not args.apply,approve_staged=args.approve_staged)
        after=catalog_health(conn)
        if args.apply:conn.commit()
        else:conn.rollback()
        report={'backup_path':backup,'before':before,'result':result,'after':after}
        content=json.dumps(report,indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(content+'\n')
        print(content)
    finally:conn.close()


if __name__=='__main__':main()
