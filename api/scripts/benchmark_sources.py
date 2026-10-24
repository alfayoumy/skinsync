"""Bounded source coverage benchmark. No catalog publication or identity changes.

Manifest: [{"source_id":"cerave_eg","url":"https://...","expected_canonical_id":123}]
Expected IDs are optional reviewed labels, not inferred ground truth.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from urllib.robotparser import RobotFileParser
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import DB_PATH,init_db,get_connection
from pipeline.source_registry import get_source,url_matches_source
from pipeline.source_collection import _read,USER_AGENT
from pipeline.source_adapters import HTMLAdapter,get_adapter
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.inci_tokenizer import tokenize_inci_string


def benchmark(conn,manifest,fetcher=_read):
    if not manifest or len(manifest)>100:raise ValueError('Benchmark requires 1-100 explicit product-page URLs.')
    reports=defaultdict(lambda:{'discovered_products':0,'ingredient_candidates':0,'full_inci_candidates':0,
        'matched_canonicals':set(),'ambiguous_records':0,'reviewed_auto_links':0,'false_match_cases':[],'outcomes':[]})
    robots={};last={}
    for entry in manifest:
        source=get_source(conn,entry['source_id']);url=entry['url']
        if not url_matches_source(source,url):raise ValueError('Manifest URL is outside the registered source.')
        report=reports[source['id']]
        try:
            if source['id'] not in robots:
                policy=RobotFileParser();text,_=fetcher('https://'+source['domain']+'/robots.txt');policy.parse(text.splitlines());robots[source['id']]=policy
            policy=robots[source['id']]
            if not policy.can_fetch(USER_AGENT,url):
                report['outcomes'].append({'url':url,'status':'disallowed_by_robots'});continue
            delay=max(float(source['rate_limit_seconds']),policy.crawl_delay(USER_AGENT) or 0)
            if delay>60:raise ValueError('Crawl delay requires a scheduled benchmark.')
            wait=delay-(time.monotonic()-last.get(source['id'],0))
            if wait>0:time.sleep(wait)
            last[source['id']]=time.monotonic()
            html,final_url=fetcher(url)
            if not url_matches_source(source,final_url):raise ValueError('Unexpected redirect domain.')
            adapter=HTMLAdapter(source)
            if html.lstrip().startswith(('{','[')):
                observations=get_adapter(source).parse(json.loads(html))
            else:observations=adapter.parse(html,url)
            for observation in observations:
                report['discovered_products']+=1
                declaration=observation.ingredient_text
                full=bool(declaration and observation.ingredient_order_verified and tokenize_inci_string(declaration,observation.raw_title).completeness=='full')
                report['ingredient_candidates']+=bool(declaration)
                report['full_inci_candidates']+=full
                identity=extract_identity(observation.raw_title,observation.raw_brand,source['market'])
                decision=CanonicalIdentityResolver(conn).resolve(identity,observation.raw_brand,observation.raw_title,source['id'],observation.source_product_id,
                    observation.source_gtin,observation.manufacturer_sku)
                if decision.status=='matched':report['matched_canonicals'].add(decision.canonical_product_id)
                elif decision.status=='ambiguous':report['ambiguous_records']+=1
                outcome={'url':url,'title':observation.raw_title,'brand':observation.raw_brand,'full_inci':full,'identity':decision.to_dict()}
                if entry.get('expected_canonical_id') is not None and decision.status=='matched':
                    report['reviewed_auto_links']+=1
                    if decision.canonical_product_id!=entry['expected_canonical_id']:report['false_match_cases'].append(outcome)
                report['outcomes'].append(outcome)
        except Exception as exc:report['outcomes'].append({'url':url,'status':'collection_failed','reason':str(exc)})
    result={}
    for sid,report in reports.items():
        report['matched_canonicals']=sorted(report['matched_canonicals'])
        result[sid]=report
    return {'sources':result,'catalog_publications':0,'source_database_writes':0,'labeled_precision_claim':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='skinsync-source-benchmark-') as directory:
        source=sqlite3.connect(Path(DB_PATH).resolve().as_uri()+'?mode=ro',uri=True);destination=sqlite3.connect(Path(directory)/'catalog.db')
        try:source.backup(destination)
        finally:source.close();destination.close()
        conn=get_connection(str(Path(directory)/'catalog.db'))
        try:
            init_db(conn=conn,seed_defaults=False)
            result=benchmark(conn,json.loads(args.manifest.read_text()))
            args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
            print(json.dumps({sid:{k:v for k,v in report.items() if k not in ('outcomes','false_match_cases')}
                for sid,report in result['sources'].items()},indent=2))
        finally:conn.close()

if __name__=='__main__':main()
