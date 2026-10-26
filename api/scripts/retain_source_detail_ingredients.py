"""Validate and retain fetched whole-page ingredient evidence; preview by default."""
import argparse
import json
import sys
import sqlite3
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.catalog_reset import _backup,guard_catalog_writes
from pipeline.saved_identity_rematch import rematch_execution_blockers
from pipeline.source_detail_ingredients import retain_detail_ingredients


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pages',type=Path,required=True);p.add_argument('--apply',action='store_true')
    p.add_argument('--approve-staged',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args();pages=json.loads(a.pages.read_text())
    if not isinstance(pages,list) or not 1<=len(pages)<=100:raise ValueError('Provide one to 100 fetched pages.')
    c=get_connection();preview_folder=None
    try:
        guard_catalog_writes(c)
        if not a.apply:
            # Preview on a consistent isolated backup so concurrent matching
            # neither blocks it nor sees temporary enrichment writes.
            preview_folder=tempfile.TemporaryDirectory(prefix='catalog-detail-preview-')
            snapshot=sqlite3.connect(str(Path(preview_folder.name)/'preview.sqlite3'))
            snapshot.row_factory=sqlite3.Row
            c.backup(snapshot);c.close();c=snapshot
        if a.apply and not rematch_execution_blockers(c)['can_start']:raise ValueError('A catalog worker is active.')
        backup=str(_backup(c,Path(c.execute('PRAGMA database_list').fetchone()[2]))) if a.apply else None
        c.execute('BEGIN IMMEDIATE' if a.apply else 'BEGIN');results=[]
        for page in pages:
            rows=list(c.execute('''SELECT id FROM source_product_observations o WHERE source_url=?
                AND id=(SELECT MAX(id) FROM source_product_observations WHERE source_id=o.source_id AND source_product_id=o.source_product_id)''',(page.get('url'),)))
            if len(rows)!=1:
                results.append({'url':page.get('url'),'status':'preserved','reason':'URL does not identify exactly one current observed source product.'});continue
            try:result=retain_detail_ingredients(c,rows[0][0],page,approve=a.approve_staged)
            except ValueError as e:result={'status':'preserved','reason':str(e)}
            results.append({'url':page.get('url'),**result})
        if a.apply:c.commit()
        else:c.rollback()
        report={'applied':a.apply,'backup_path':backup,'model_calls':0,'preview_scope':None if a.apply else 'consistent_isolated_snapshot','results':results}
        value=json.dumps(report,indent=2)
        if a.output:a.output.write_text(value+'\n')
        print(value)
    finally:
        c.close()
        if preview_folder:preview_folder.cleanup()


if __name__=='__main__':main()
