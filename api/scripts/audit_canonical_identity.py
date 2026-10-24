"""Inspect canonical identity conflicts without writing to the catalog database."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import DB_PATH
from pipeline.identity_review import audit_canonical_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write detailed review evidence to this JSON file.')
    args = parser.parse_args()
    conn = sqlite3.connect(Path(DB_PATH).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        result = audit_canonical_identity(conn)
        summary = {key: value for key, value in result.items() if key != 'findings'}
        summary['by_kind'] = dict(Counter(f['kind'] for f in result['findings']))
        summary['database_writes'] = conn.total_changes
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
