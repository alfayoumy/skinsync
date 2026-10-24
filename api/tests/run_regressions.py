"""Run the entire unittest suite on a disposable catalog snapshot, without live APIs.

Usage: python3 api/tests/run_regressions.py
The source catalog is read-only. HTTP requests use FastAPI TestClient; external
scraping, remote models and provider calls are replaced by deterministic stubs.
"""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


def main():
    api = Path(__file__).resolve().parents[1]
    source_path = Path(os.environ.get('SKINSYNC_DB_PATH', api / 'skincare.db')).resolve()
    with tempfile.TemporaryDirectory(prefix='skinsync-regression-') as directory:
        snapshot = Path(directory) / 'catalog.db'
        source = sqlite3.connect(f'file:{source_path}?mode=ro', uri=True)
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        os.environ['SKINSYNC_DB_PATH'] = str(snapshot)
        os.environ['ENABLE_CROSS_ENCODER'] = 'false'
        sys.path.insert(0, str(api))
        from pipeline.db_manager import init_db
        init_db(seed_defaults=False)
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch('urllib.request.urlopen', side_effect=RuntimeError('External API disabled in regression run')))
            stack.enter_context(patch('pipeline.inci_matcher.get_embedding_model', return_value=None))
            stack.enter_context(patch('pipeline.inci_matcher.get_cross_encoder', return_value=None))
            stack.enter_context(patch('routers.catalog.refresh_catalog', return_value={'status': 'success', 'added': 0, 'updated': 0}))
            suite = unittest.defaultTestLoader.discover(str(api / 'tests'))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
