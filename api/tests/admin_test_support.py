"""Explicit test authentication; never imported by application code."""
import json
from unittest.mock import patch
TOKEN = 'skinsync-unit-test-administrator-token-only'
RECORDS = json.dumps({'test-admin': {'token': TOKEN, 'permissions': ['read','collection','identity','formula','formula_correction','operations']}})
def authenticate(testcase):
    patcher = patch.dict('os.environ', {'SKINSYNC_ADMIN_CREDENTIALS': RECORDS})
    patcher.start()
    cleanup = getattr(testcase, 'addClassCleanup', None) if isinstance(testcase, type) else testcase.addCleanup
    cleanup(patcher.stop)
    return {'Authorization': 'Bearer ' + TOKEN}
