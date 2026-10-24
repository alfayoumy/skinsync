"""Read-only catalog baseline; writes only the requested JSON report."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import DB_PATH
from pipeline.catalog_diagnostics import profile_catalog

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    conn=sqlite3.connect(Path(DB_PATH).resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        result=profile_catalog(conn)
        args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
        print(json.dumps(result['counts'],indent=2))
    finally:conn.close()
