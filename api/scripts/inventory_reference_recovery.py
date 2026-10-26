"""Read-only inventory of exact-name reference candidates; never approves identities or formulas."""
import argparse
from collections import defaultdict, Counter
from functools import lru_cache
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.db_manager import get_connection
from pipeline.brand_registry import resolve_brand
from pipeline.canonical_identity_resolver import identity_name, extract_identity, compare_attributes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    conn=get_connection()
    try:
        conn.execute('BEGIN')
        @lru_cache(maxsize=None)
        def brand(value):
            found=resolve_brand(value,conn)
            return (found['id'],found['name']) if found and found.get('verified') and found.get('id') else None
        candidates=list(conn.execute("""SELECT id,canonical_title,brand,identity_status,identity_attributes
            FROM canonical_products WHERE is_skincare=1 AND identity_status NOT IN ('redirected','reversed_split')
            AND COALESCE(TRIM(ingredients_raw),'')=''"""))
        index=defaultdict(list)
        for row in candidates:
            b=brand(row['brand'])
            if b:index[(b[0],identity_name(row['canonical_title'],b[1]))].append(dict(row))
        matches=defaultdict(list)
        reference_count=0
        for row in conn.execute('SELECT id,brand,product_name,ingredients,source FROM inci_reference'):
            reference_count+=1
            b=brand(row['brand'])
            if not b:continue
            key=(b[0],identity_name(row['product_name'],b[1]))
            for product in index.get(key,[]):
                attrs=json.loads(product['identity_attributes'] or '{}')
                check=compare_attributes(extract_identity(product['canonical_title'],b[1],attributes=attrs),extract_identity(row['product_name'],b[1]))
                matches[product['id']].append({'reference_id':row['id'],'reference_title':row['product_name'],
                    'reference_source':row['source'],'ingredient_characters':len(row['ingredients'] or ''),
                    'attribute_comparison':check,'competing_canonicals_at_name_key':[x['id'] for x in index[key] if x['id']!=product['id']]})
        products=[{'canonical_id':p['id'],'title':p['canonical_title'],'brand':p['brand'],
                   'identity_status':p['identity_status'],'references':matches[p['id']]} for p in candidates if matches[p['id']]]
        report={'read_only':True,'model_calls':0,'reference_rows_checked':reference_count,
            'missing_skincare_products_checked':len(candidates),'products_with_exact_name_candidates':len(products),
            'candidate_identity_status_counts':dict(Counter(p['identity_status'] for p in products)),
            'reference_source_counts':dict(Counter(r['reference_source'] for p in products for r in p['references'])),
            'limitations':['Name equivalence is only candidate discovery, not independent identity verification.',
                'Reference rows do not retain market or per-product evidence URLs.',
                'store_verified references may derive from this catalog and cannot supply independent corroboration.',
                'Regional formulas, variants, ingredient completeness and competing products require review before any publication.'],
            'products':products}
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='products'}))
    finally:
        conn.rollback();conn.close()

if __name__=='__main__':main()
