"""Prepare two-product comparisons; archive old group answers; never calls Gemini."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.catalog_reset import _backup,guard_catalog_writes
from pipeline.saved_identity_rematch import rematch_execution_blockers
from pipeline.prepare_identity_pairs import prepare_identity_pairs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--review-id',type=int,action='append')
    parser.add_argument('--refresh-pairwise',action='store_true',help='Refresh only explicitly selected unresolved pairwise cases; preserve completed pair answers.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();conn=get_connection()
    try:
        guard_catalog_writes(conn)
        if not rematch_execution_blockers(conn)['can_start']:raise ValueError('A catalog worker is active.')
        backup=str(_backup(conn,Path(conn.execute('PRAGMA database_list').fetchone()[2]))) if args.apply else None
        conn.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
        result=prepare_identity_pairs(conn,apply=args.apply,review_ids=args.review_id,refresh_pairwise=args.refresh_pairwise)
        if args.apply:conn.commit()
        else:conn.rollback()
        report={'backup_path':backup,'result':result};value=json.dumps(report,indent=2)
        if args.output:args.output.write_text(value+'\n')
        print(value)
    finally:conn.close()


if __name__=='__main__':main()
