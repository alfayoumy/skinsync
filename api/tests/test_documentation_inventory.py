"""Documentation inventory regression checks.

Fail on missing documented HTTP routes, declared SQLite tables, broken relative
markdown links or machine-specific file links. Code is the source of truth.
"""
import re
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[2]
DOCS=ROOT/'docs'
API=ROOT/'api'
ROUTES=API/'routers'


class DocumentationInventoryTests(unittest.TestCase):
    def test_every_http_route_is_in_api_reference(self):
        content=(DOCS/'api-reference.md').read_text(encoding='utf-8')
        total=0
        for file in ROUTES.glob('*.py'):
            source=file.read_text(encoding='utf-8')
            prefix_match=re.search(r'APIRouter\(\s*prefix\s*=\s*[\'"]([^\'"]*)',source)
            prefix=prefix_match.group(1) if prefix_match else ''
            for method,path in re.findall(r'@router\.(get|post|patch|put|delete)\(\s*[\'"]([^\'"]+)',source):
                endpoint=prefix+path
                with self.subTest(router=file.name,method=method,endpoint=endpoint):
                    self.assertIn(chr(96)+endpoint+chr(96),content)
                total+=1
        self.assertGreater(total,100)

    def test_every_schema_table_is_in_database_reference(self):
        doc=(DOCS/'database-reference.md').read_text(encoding='utf-8')
        for path in (API/'pipeline/db_manager.py',API/'pipeline/aggregation_schema.py',
                     API/'pipeline/identity_review.py'):
            source=path.read_text(encoding='utf-8')
            tables=set(re.findall(r'CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z_0-9]*)\s*\(',source,re.I))
            for table in sorted(tables):
                with self.subTest(module=path.name,table=table):
                    self.assertIn(chr(96)+table+chr(96),doc)

    def test_documentation_has_no_machine_links_or_broken_relative_links(self):
        for path in [*DOCS.rglob('*.md'),ROOT/'skinsync-app/README.md']:
            content=path.read_text(encoding='utf-8')
            with self.subTest(file=str(path.relative_to(ROOT)),check='local_path'):
                self.assertNotIn('file:///',content)
                self.assertNotIn('/mnt/windows/',content)
            for uri in re.findall(r'\]\(([^)\s]+)(?:\s+["\'][^)]*)?\)',content):
                clean=unquote(uri.split('#',1)[0].split('?',1)[0])
                if not clean or clean.startswith(('https://','http://','mailto:','data:','#')):
                    continue
                with self.subTest(file=str(path.relative_to(ROOT)),target=clean):
                    self.assertTrue((path.parent/clean).exists(),f'Broken link {clean} in {path}')


if __name__=='__main__':
    unittest.main()
