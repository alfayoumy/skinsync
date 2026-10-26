"""Review selected invalid publications; preview by default, preserve all history."""
import argparse
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.catalog_reset import _backup,guard_catalog_writes
from pipeline.saved_identity_rematch import rematch_execution_blockers
from pipeline.formulation_service import FormulationRepository
from pipeline.inci_tokenizer import tokenize_inci_string
from pipeline.source_detail_ingredients import retain_detail_ingredients
from pipeline.aggregation_service import queue_review


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--canonical-id',type=int,action='append',required=True)
    p.add_argument('--repair-share',action='store_true',help='Revalidate a retained exact page after removing only its trailing Share widget.')
    p.add_argument('--apply',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();c=get_connection()
    try:
        guard_catalog_writes(c)
        if not rematch_execution_blockers(c)['can_start']:raise ValueError('A catalog worker is active.')
        backup=str(_backup(c,Path(c.execute('PRAGMA database_list').fetchone()[2]))) if a.apply else None
        c.execute('BEGIN IMMEDIATE' if a.apply else 'BEGIN');results=[]
        for cid in dict.fromkeys(a.canonical_id):
            row=c.execute('SELECT * FROM canonical_products WHERE id=?',(cid,)).fetchone()
            if not row:raise ValueError('Canonical not found: '+str(cid))
            if not row['ingredients_raw']:
                results.append({'canonical_id':cid,'status':'already_unpublished'});continue
            validation=tokenize_inci_string(row['ingredients_raw'],product_title=row['canonical_title'])
            if validation.completeness=='full':
                results.append({'canonical_id':cid,'status':'preserved_valid'});continue
            reason='Reviewed published declaration fails current completeness checks: '+json.dumps(validation.validation_flags,sort_keys=True)+'. Preserve original evidence; do not infer or reconstruct missing chemical names.'
            withdrawal=FormulationRepository().withdraw_formulation(cid,'catalog_quality_repair_agent',reason,row['formula_hash'],conn=c)
            result={'canonical_id':cid,'title':row['canonical_title'],'status':'withdrawn','withdrawal':withdrawal,'reason':reason}
            if a.repair_share and re.search(r'\bShare\s*:\s*$',row['ingredients_raw'],re.I):
                pages=[]
                for obs in c.execute('''SELECT DISTINCT o.* FROM source_product_observations o
                    JOIN product_offers p ON p.observation_id=o.id WHERE p.canonical_product_id=?
                    AND o.id=(SELECT MAX(id) FROM source_product_observations
                              WHERE source_id=o.source_id AND source_product_id=o.source_product_id)''',(cid,)):
                    detail=json.loads(obs['raw_payload'] or '{}').get('detail_ingredient_evidence') or {}
                    if detail.get('url')==row['ingredient_source_url'] and detail.get('text'):
                        pages.append((obs['id'],{'url':detail['url'],'text':detail['text']}))
                if len(pages)==1:
                    # Savepoint in retention rolls back a rejected page; a
                    # failed repair must not restore the invalid publication.
                    try:correction=retain_detail_ingredients(c,pages[0][0],pages[0][1],approve=True)
                    except ValueError as exc:correction={'status':'preserved','reason':str(exc)}
                    result['correction']=correction
                    if correction['status']=='published':result['status']='corrected_from_retained_page'
            if result['status']=='withdrawn':
                result['review_id']=queue_review(c,'formulation',None,None,cid,
                    {'method':'published_declaration_quality_review','reason':reason,'withdrawal_evidence_id':withdrawal['evidence_id']})
            results.append(result)
        if a.apply:c.commit()
        else:c.rollback()
        report={'applied':a.apply,'backup_path':backup,'model_calls':0,'results':results}
        a.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    finally:c.close()


if __name__=='__main__':main()
