"""Bounded whole-page acquisition for known, verified retailer associations.

Read-only: produces fetched pages and a resumable report, never publishes INCI.
"""
import argparse
import asyncio
import json
import hashlib
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.source_registry import get_source
from pipeline.inci_research import _donsetch_pages
from pipeline.identity_trust import pending_identity_ids


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--limit',type=int,default=10)
    p.add_argument('--after-canonical-id',type=int,default=0)
    p.add_argument('--pages-output',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args()
    if not 1<=a.limit<=100:p.error('Limit must be one to 100 pages.')
    if a.pages_output.resolve()==a.report.resolve():p.error('Pages and report need different files.')
    a.pages_output.parent.mkdir(parents=True,exist_ok=True)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    c=get_connection()
    try:
        s=get_source(c,a.source)
        if not s['enabled'] or s['source_type']!='retailer':raise ValueError('An enabled retailer source is required.')
        delay=float(s['rate_limit_seconds'] or 0)
        if delay>60:raise ValueError('Source requires a scheduled collection delay.')
        disputed=pending_identity_ids(c)
        rows=[dict(r) for r in c.execute("""SELECT DISTINCT c.id canonical_id,o.id observation_id,
            o.raw_title,o.raw_brand,o.source_url FROM canonical_products c
            JOIN product_offers p ON p.canonical_product_id=c.id
            JOIN source_product_observations o ON o.id=p.observation_id
            JOIN product_identity_links l ON l.source_id=o.source_id AND l.source_product_id=o.source_product_id
                AND l.canonical_product_id=c.id AND l.status='accepted' AND l.identity_status='identity_verified'
            WHERE c.id>? AND c.identity_status='verified' AND c.is_skincare=1
            AND COALESCE(TRIM(c.ingredients_raw),'')='' AND o.source_id=?
            -- Feed summaries can contain only highlighted actives. They must
            -- not prevent acquisition of the actual product-page declaration.
            -- Previously retained detail pages are skipped to avoid repeatedly
            -- fetching the same known partial declaration.
            AND json_extract(o.raw_payload,'$.detail_ingredient_evidence') IS NULL
            AND o.id=(SELECT MAX(id) FROM source_product_observations WHERE source_id=o.source_id AND source_product_id=o.source_product_id)
            ORDER BY c.id,o.id""",(a.after_canonical_id,a.source)) if r['canonical_id'] not in disputed]
        chosen=[];seen=set()
        for r in rows:
            if r['canonical_id'] in seen:continue
            if c.execute("SELECT 1 FROM source_product_variants WHERE observation_id=? AND json_extract(attributes,'$.attributes')!='{}' LIMIT 1",(r['observation_id'],)).fetchone():continue
            seen.add(r['canonical_id']);chosen.append(r)
            if len(chosen)==a.limit:break
    finally:c.close()
    pages=[];results=[];last=0
    report={'source_id':a.source,'model_calls':0,'read_only':True,'selected':len(chosen),
            'results':results,'last_canonical_id':a.after_canonical_id,'more_candidates_in_selection':len(rows)>len(chosen),
            'status':'running','selected_products':chosen,'pages_output':str(a.pages_output.resolve())}
    def persist():
        data=json.dumps(pages,ensure_ascii=False)+'\n'
        temporary=a.pages_output.with_suffix(a.pages_output.suffix+'.tmp')
        temporary.write_text(data);temporary.replace(a.pages_output)
        report['pages_sha256']=hashlib.sha256(data.encode()).hexdigest()
        temporary=a.report.with_suffix(a.report.suffix+'.tmp')
        temporary.write_text(json.dumps(report,indent=2)+'\n');temporary.replace(a.report)
    persist()
    for item in chosen:
        pause=delay-(time.monotonic()-last)
        if pause>0:time.sleep(pause)
        last=time.monotonic()
        try:
            found=asyncio.run(_donsetch_pages(item['raw_brand'],item['raw_title'],{s['domain'].removeprefix('www.')},1,[item['source_url']]))
            if not found:raise RuntimeError('No readable page returned.')
            pages.extend(found);result={**item,'status':'fetched','pages':len(found)}
        except Exception as e:result={**item,'status':'error','error_type':type(e).__name__}
        results.append(result);report['last_canonical_id']=item['canonical_id']
        persist()
        print(json.dumps(result),flush=True)
    report['status']='completed'
    persist()


if __name__=='__main__':main()
