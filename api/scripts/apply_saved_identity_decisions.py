"""Preview/apply saved semantic identity decisions; never calls a provider."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.saved_gemini_decisions import apply_saved_decisions
from pipeline.saved_identity_rematch import rematch_execution_blockers
from pipeline.catalog_reset import _backup,guard_catalog_writes
from pipeline.catalog_health import catalog_health


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--confidence',type=float,default=.95)
    parser.add_argument('--limit',type=int,default=5000)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    conn=get_connection(args.db) if args.db else get_connection()
    try:
        guard_catalog_writes(conn)
        if not rematch_execution_blockers(conn)['can_start']:
            raise ValueError('A catalog worker is active; finish it before applying saved decisions.')
        before=catalog_health(conn)
        backup=str(_backup(conn,Path(conn.execute('PRAGMA database_list').fetchone()[2]))) if args.apply else None
        conn.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
        if not rematch_execution_blockers(conn)['can_start']:
            raise ValueError('A worker started before the catalog transaction was acquired.')
        result=apply_saved_decisions(conn,limit=args.limit,threshold=args.confidence,dry_run=not args.apply)
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
