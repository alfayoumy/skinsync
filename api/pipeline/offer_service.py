"""Trade-item offers: packaging and price are separate from marketed formulation."""
import re
from pipeline.canonical_identity_resolver import valid_gtin


def package_details(title,variant_title='',price=0):
    text=f'{variant_title} {title}'
    match=re.search(r'(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|ml|gm|kg|g|l|oz)\b',text,re.I)
    if not match:return dict(pack_count=1,unit_size=None,unit_size_unit=None,total_quantity=None,normalized_size_ml=None,
                             normalized_size_g=None,unit_price=None,unit_measure=None)
    count=int(match[1] or 1)
    pack=re.search(r'\b(?:pack of|multipack|pack)\s*(\d+)\b',text,re.I)
    if pack and not match[1]:count=int(pack[1])
    if not 1<=count<=10000:raise ValueError('Invalid pack count.')
    size=float(match[2]);unit=match[3].lower().replace('.','').replace(' ','')
    if size<=0:raise ValueError('Package size must be positive.')
    if unit=='gm':unit='g'
    normalized=size*({'l':1000,'kg':1000,'floz':29.5735,'oz':28.3495}.get(unit,1))*count
    liquid=unit in ('l','ml','floz')
    measure='EGP / 100ml' if liquid else 'EGP / 100g'
    return dict(pack_count=count,unit_size=size,unit_size_unit=unit,total_quantity=size*count,
                normalized_size_ml=round(normalized,4) if liquid else None,normalized_size_g=None if liquid else round(normalized,4),
                unit_price=round(price*100/normalized,2) if price>0 else None,unit_measure=measure if price>0 else None)


def upsert_offer(conn,source,observation,variant,canonical_id,observation_id,legacy_product_id=None,fields_filter=None):
    if source['source_type']!='retailer' or not source['provides_prices']:
        return None
    if variant.currency!='EGP':
        raise ValueError('Currency conversion must be explicit before creating Egyptian price offers.')
    from pipeline.source_adapters.contracts import money
    validated_price=money(variant.price)
    validated_compare_price=money(variant.compare_at_price)
    package=package_details(observation.raw_title,variant.title,validated_price)
    fields=dict(source_id=source['id'],source_product_id=observation.source_product_id,source_variant_id=variant.source_variant_id,
        canonical_product_id=canonical_id,legacy_product_id=legacy_product_id,observation_id=observation_id,
        product_url=observation.source_url,title=observation.raw_title,sku=variant.sku,gtin=valid_gtin(variant.gtin),price=validated_price,
        currency=variant.currency,compare_at_price=validated_compare_price,is_available=int(variant.available),
        image_url=observation.images[0] if observation.images else None,**package)
    columns=list(fields)
    mutable=set(columns)-{'source_id','source_product_id','source_variant_id'}
    if fields_filter is not None:
        scopes={'price':{'price','compare_at_price','unit_price','unit_measure'},'stock':{'is_available'},
                'image':{'image_url'},'title':{'title','pack_count','unit_size','unit_size_unit','total_quantity','normalized_size_ml','normalized_size_g'}}
        mutable={'canonical_product_id','legacy_product_id','observation_id','sku','gtin'} | set().union(*(scopes.get(k,set()) for k in fields_filter))
    updates=','.join(f'{k}=excluded.{k}' for k in columns if k in mutable)
    conn.execute(f'''INSERT INTO product_offers({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})
        ON CONFLICT(source_id,source_product_id,source_variant_id) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP''',list(fields.values()))
    return conn.execute('SELECT id FROM product_offers WHERE source_id=? AND source_product_id=? AND source_variant_id=?',
        (source['id'],observation.source_product_id,variant.source_variant_id)).fetchone()[0]


def refresh_offer_aggregates(conn,canonical_id):
    # Suppress a legacy product-page representative once real variant offers exist.
    rows=conn.execute('''SELECT o.source_id,o.price,o.is_available FROM product_offers o JOIN sources s ON s.id=o.source_id WHERE o.canonical_product_id=? AND s.provides_prices=1 AND (s.wave<=1 OR s.publish_enabled=1) AND o.currency='EGP'
        UNION ALL SELECT p.store_id,p.price,p.is_available FROM products p WHERE p.canonical_product_id=?
        AND NOT EXISTS(SELECT 1 FROM product_offers o JOIN sources s ON s.id=o.source_id WHERE o.legacy_product_id=p.id AND s.provides_prices=1 AND (s.wave<=1 OR s.publish_enabled=1))''',(canonical_id,canonical_id)).fetchall()
    active=[r for r in rows if r[1]>0 and r[2]] or [r for r in rows if r[1]>0]
    prices=[r[1] for r in active]
    conn.execute('UPDATE canonical_products SET min_price=?,max_price=?,store_count=? WHERE id=?',
        (min(prices) if prices else 0,max(prices) if prices else 0,len({r[0] for r in rows}),canonical_id))
