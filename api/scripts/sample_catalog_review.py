"""Export stratified REAL catalog records for independent labeling; no inferred gold labels."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import sqlite3


def export_samples(database,output,per_category=20):
    conn=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        buckets={}
        for row in conn.execute('SELECT id,store_id,title,brand,category,is_skincare,canonical_product_id,product_url FROM products ORDER BY id'):
            buckets.setdefault(row['category'] or 'Unknown',[]).append(dict(row))
        rng=random.Random(354181);samples=[]
        for category,rows in sorted(buckets.items()):
            samples.extend(rng.sample(rows,min(per_category,len(rows))))
        output=Path(output);output.mkdir(parents=True,exist_ok=False)
        fields=['id','store_id','title','brand','category','is_skincare','canonical_product_id','product_url','reviewed_category','reviewed_is_skincare','reviewer','reason']
        with (output/'classification.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(samples)
        pairs=[]
        for sample in samples:
            if not sample['canonical_product_id']:continue
            canonical=conn.execute('SELECT canonical_title,brand FROM canonical_products WHERE id=?',(sample['canonical_product_id'],)).fetchone()
            if canonical:pairs.append({'product_id':sample['id'],'source_id':sample['store_id'],'title':sample['title'],'brand':sample['brand'],
                'canonical_id':sample['canonical_product_id'],'canonical_title':canonical[0],'canonical_brand':canonical[1],
                'reviewed_same_identity':'','reviewer':'','reason':''})
        with (output/'identity-pairs.csv').open('w',newline='') as f:
            fields=list(pairs[0]) if pairs else ['product_id','reviewed_same_identity','reviewer','reason']
            writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(pairs)
        manifest={'seed':354181,'classification_samples':len(samples),'identity_pairs':len(pairs),'categories':{k:len(v) for k,v in buckets.items()},
                  'status':'unlabeled; not benchmark evidence','files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.csv')}}
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
        return manifest
    finally:conn.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(json.dumps(export_samples(args.database,args.output),indent=2))
