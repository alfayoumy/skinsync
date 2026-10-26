"""Run a bounded, resumable queue of strictly two-product Gemini requests."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.saved_identity_rematch import rematch_execution_blockers
from pipeline.task_tracker import create_task
from pipeline.product_identity_gemini import run_identity_gemini_queue


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests',type=int,default=500)
    parser.add_argument('--review-id',type=int)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.requests<=20000:parser.error('Request budget must be 1..20000.')
    conn=get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        if not rematch_execution_blockers(conn)['can_start']:raise ValueError('A catalog worker is active.')
        task=create_task('product_identity_gemini',target_source='catalog_repair',total_items=args.requests,
            params={'maximum_pair_requests':args.requests,'products_per_request':2,'review_id':args.review_id},
            auto_start=True,conn=conn)
    finally:conn.close()
    print(json.dumps({'task_id':task,'products_per_request':2,'maximum_pair_requests':args.requests}),flush=True)
    result=run_identity_gemini_queue(task,limit=args.requests,batch_size=1,review_id=args.review_id)
    report={'task_id':task,'result':result}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
