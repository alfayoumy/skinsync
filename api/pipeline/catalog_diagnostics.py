"""Read-only baseline and reconciliation proposals with source/canonical identifiers."""
from collections import Counter, defaultdict
import json
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.identity_review import audit_canonical_identity


def profile_catalog(conn):
    products=[dict(r) for r in conn.execute('SELECT * FROM products ORDER BY id')]
    canonicals=[dict(r) for r in conn.execute('SELECT * FROM canonical_products ORDER BY id')]
    linked=defaultdict(list)
    for product in products:
        if product.get('canonical_product_id'):linked[product['canonical_product_id']].append(product)
    proposed=[];false_exclusions=[];sizes=[]
    for p in products:
        classification=classify_product_attributes(p['title'],p['brand'],p.get('original_category') or '')
        reference={'product_id':p['id'],'canonical_product_id':p.get('canonical_product_id'),'source_id':p.get('store_id'),
                   'source_url':p.get('product_url'),'original_title':p['title']}
        if classification.primary_category!=p.get('category'):
            proposed.append({**reference,'current_category':p.get('category'),'proposal':classification.to_dict()})
        if classification.is_skincare and not p.get('is_skincare'):
            false_exclusions.append(reference)
        if not p.get('size_raw') or p.get('normalized_size_ml') and p.get('normalized_size_g'):
            sizes.append(reference)
    disagreements=[]
    for cid,members in linked.items():
        cats={p.get('category') for p in members}
        if len(cats)>1:disagreements.append({'canonical_product_id':cid,'product_ids':[p['id'] for p in members],'categories':sorted(c for c in cats if c)})
    existing={r[0] for r in conn.execute("SELECT DISTINCT canonical_product_id FROM inci_audit_evidence WHERE decision='approved_authoritative'")}
    identity=audit_canonical_identity(conn)
    barcode_conflicts=[]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_offers'").fetchone():
        for row in conn.execute('''SELECT gtin,GROUP_CONCAT(DISTINCT canonical_product_id) FROM product_offers
                WHERE gtin IS NOT NULL GROUP BY gtin HAVING COUNT(DISTINCT canonical_product_id)>1'''):
            barcode_conflicts.append({'gtin':row[0],'canonical_ids':[int(cid) for cid in row[1].split(',')]})
    return {'counts':{'retailer_records':len(products),'canonical_records':len(canonicals),
        'unlinked_records':sum(not p.get('canonical_product_id') for p in products),'source_records':dict(Counter(p['store_id'] for p in products)),
        'category_corrections':len(proposed),'possible_false_exclusions':len(false_exclusions),
        'orphan_canonicals':sum(c['id'] not in linked for c in canonicals),
        'formulations_without_approval_evidence':sum(bool(c.get('ingredients_raw')) and c['id'] not in existing for c in canonicals)},
        'anomalies':{'unlinked':[{'product_id':p['id'],'source_id':p['store_id'],'source_url':p['product_url']} for p in products if not p.get('canonical_product_id')],
            'category_corrections':proposed,'false_exclusions':false_exclusions,'category_disagreements':disagreements,
            'orphan_canonicals':[c['id'] for c in canonicals if c['id'] not in linked],
            'unverified_formulations':[c['id'] for c in canonicals if c.get('ingredients_raw') and c['id'] not in existing],
            'incomplete_sizes':sizes,'barcode_identity_conflicts':barcode_conflicts,'identity_findings':identity['findings']},'database_writes':conn.total_changes}
