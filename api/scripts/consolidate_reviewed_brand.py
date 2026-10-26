"""Consolidate explicitly reviewed brand labels; products remain distinct."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.brand_registry import consolidate_brand_labels
from pipeline.catalog_reset import _backup,guard_catalog_writes
from pipeline.saved_identity_rematch import rematch_execution_blockers
from pipeline.canonical_identity_resolver import identity_name


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--brand',required=True);p.add_argument('--alias',action='append',required=True)
    p.add_argument('--evidence-url',action='append',required=True);p.add_argument('--reason',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--output',type=Path)
    p.add_argument('--merge-exact',action='store_true',help='Apply guarded exact-title duplicate merges within this reviewed brand.')
    a=p.parse_args();c=get_connection()
    try:
        guard_catalog_writes(c)
        backup=str(_backup(c,Path(c.execute('PRAGMA database_list').fetchone()[2]))) if a.apply else None
        c.execute('BEGIN IMMEDIATE' if a.apply else 'BEGIN')
        if not rematch_execution_blockers(c)['can_start']:raise ValueError('A catalog worker is active.')
        result=consolidate_brand_labels(c,a.brand,a.alias,'catalog_repair_agent',a.reason,a.evidence_url)
        groups={}
        for r in c.execute("SELECT id,canonical_title,brand,identity_status,length(ingredients_raw) inci FROM canonical_products WHERE brand_id=? AND identity_status NOT IN ('redirected','reversed_split')",(result['brand_id'],)):
            groups.setdefault(identity_name(r['canonical_title'],r['brand']),[]).append(dict(r))
        report={'applied':a.apply,'backup_path':backup,'result':result,
                'duplicate_title_groups':[v for k,v in groups.items() if k and len(v)>1],
                'evidence_urls':a.evidence_url,'reason':a.reason,'products_merged':0}
        if a.merge_exact:
            from pipeline.catalog_repair import merge_exact_brand_duplicates
            report['duplicate_repairs']=merge_exact_brand_duplicates(c,result['brand_id'])
            report['products_merged']=sum(len(r['canonical_ids'])-1 for r in report['duplicate_repairs'] if r['action']=='merged')
        if a.apply:c.commit()
        else:c.rollback()
        text=json.dumps(report,indent=2)
        if a.output:a.output.write_text(text+'\n')
        print(text)
    finally:c.close()


if __name__=='__main__':main()
