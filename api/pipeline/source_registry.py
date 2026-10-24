"""Staged source policies. Registered does not mean approved for publication."""
import json
from urllib.parse import urlparse

# source ID, name, domain, type, adapter, market, wave
SOURCE_SPECS = [
 ('sourcebeauty','Source Beauty','sourcebeauty.com','retailer','shopify','EG',1),
 ('feel22','Feel22 Egypt','eg.feel22.com','retailer','shopify','EG',1),
 ('bloompharmacy','Bloom Pharmacy','www.bloompharmacy.com','retailer','shopify','EG',1),
 ('drdawood','Dr Dawood','drdawoodstore.com','retailer','shopify','EG',0),
 ('bashrety','Bashrety','bashrety.tv','retailer','shopify','EG',0),
 ('rawafrican','Raw African','rawafrican.net','retailer','shopify','EG',0),
 ('zynah','ZYNAH','zynah.me','retailer','shopify','EG',2),
 ('jamila','Jamila Egypt','jamila-eg.net','retailer','shopify','EG',2),
 ('zahra','Zahra Egypt','zahraegypt.com','retailer','html','EG',2),
 ('cerave_eg','CeraVe Egypt','cerave.eg','manufacturer','html','EG',3),
 ('garnier_arabia','Garnier Arabia','garnierarabia.com','manufacturer','html','MENA',3),
 ('nivea_me','NIVEA Middle East','nivea-me.com','manufacturer','html','MENA',3),
 ('skin1004','SKIN1004','skin1004.com','manufacturer','shopify','global',3),
 ('cosrx','COSRX','cosrx.com','manufacturer','shopify','global',3),
 ('beautyofjoseon','Beauty of Joseon','beautyofjoseon.com','manufacturer','shopify','global',3),
 ('anua','Anua','anua.com','manufacturer','shopify','US',3),
 ('purito','PURITO Seoul','purito.com','manufacturer','html','global',3),
 ('roundlab','Round Lab','roundlab.com','manufacturer','shopify','global',3),
 ('theordinary','The Ordinary','theordinary.com','manufacturer','html','global',3),
 ('larocheposay_us','La Roche-Posay US','laroche-posay.us','manufacturer','html','US',3),
 ('vichy_us','Vichy US','vichyusa.com','manufacturer','html','US',3),
 ('inkeedecoder','INKEEDecoder','inkeedecoder.com','reference','html','global',4),
 ('incidecoder','INCIdecoder legacy domain','incidecoder.com','reference','html','global',4),
 ('skinsort','SkinSort','skinsort.com','reference','html','global',4),
 ('skinsafe','SkinSAFE','skinsafeproducts.com','reference','html','global',4),
 ('openbeautyfacts','Open Beauty Facts','world.openbeautyfacts.org','reference','openbeautyfacts','global',4),
 ('skincarisma','Skincarisma','skincarisma.com','reference','html','global',4),
 ('arvea_eg','ARVEA Egypt','arvea-nature.com','manufacturer','html','EG',5),
 ('noon_eg','Noon Egypt','noon.com','retailer','html','EG',5),
 ('amazon_eg','Amazon Egypt','amazon.eg','retailer','html','EG',5),
 ('seif','Seif Pharmacies','seif-online.com','retailer','html','EG',5),
]
DEFAULT_RULES = {'full_headers':["what's in it",'ingredients','full ingredients','full ingredients list','list of ingredients','المكونات'],
                 'partial_headers':['key ingredients','key active ingredients','active ingredients','مكونات فعالة']}


def seed_sources(conn):
    stores={r['id']:dict(r) for r in conn.execute('SELECT * FROM stores')}
    official_brands={'cerave_eg':'CeraVe','garnier_arabia':'Garnier','nivea_me':'NIVEA',
                     'larocheposay_us':'La Roche-Posay','vichy_us':'Vichy','purito':'PURITO'}
    for sid,name,domain,kind,adapter,market,wave in SOURCE_SPECS:
        existing=stores.get(sid)
        enabled=int(bool(existing and existing.get('is_active')))
        conn.execute('''INSERT OR IGNORE INTO sources(id,name,domain,source_type,adapter_type,market,provides_prices,
            reliability,wave,enabled,section_rules) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (sid,name,domain,kind,adapter,market,int(kind=='retailer'),
             'regional_official' if kind=='manufacturer' and market in ('EG','MENA') else
             ('official_other_market' if kind=='manufacturer' else ('local_retailer' if kind=='retailer' else 'reference')),
             wave,enabled,json.dumps({**DEFAULT_RULES,**({'official_brand':official_brands[sid]} if sid in official_brands else {})})))
    for sid,store in stores.items():
        if conn.execute('SELECT 1 FROM sources WHERE id=?',(sid,)).fetchone(): continue
        conn.execute('''INSERT OR IGNORE INTO sources(id,name,domain,source_type,adapter_type,market,provides_prices,enabled,section_rules)
            VALUES (?,?,?,'retailer',?,'EG',1,?,?)''',
            (sid,store['name'],store['domain'],store.get('platform') or 'shopify',int(store.get('is_active',0)),json.dumps(DEFAULT_RULES)))


def get_source(conn,source_id):
    row=conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
    if not row: raise ValueError('Unknown source ID: '+str(source_id))
    result=dict(row); result['section_rules']=json.loads(result['section_rules'])
    return result


def url_matches_source(source,url):
    parsed=urlparse(url)
    host=(parsed.hostname or '').lower().removeprefix('www.')
    domain=source['domain'].lower().removeprefix('www.')
    return parsed.scheme=='https' and (host==domain or host.endswith('.'+domain)) and not parsed.username


def list_source_inventory(conn):
    """Registry inventory includes staged sources even before their first scrape."""
    return [dict(row) for row in conn.execute('''SELECT s.*,
        COALESCE(p.legacy_products,0) AS legacy_products,
        COALESCE(p.with_ingredients,0) AS with_ingredients,p.avg_price,
        COALESCE(ob.observed_products,0) AS observed_products,
        COALESCE(v.variant_offers,0) AS variant_offers,
        COALESCE(t.total_products,0) AS total_products
        FROM sources s
        LEFT JOIN (SELECT store_id,COUNT(*) AS legacy_products,
            SUM(CASE WHEN ingredients_raw IS NOT NULL AND LENGTH(TRIM(ingredients_raw))>=15 THEN 1 ELSE 0 END) AS with_ingredients,
            ROUND(AVG(price),2) AS avg_price FROM products GROUP BY store_id) p ON p.store_id=s.id
        LEFT JOIN (SELECT source_id,COUNT(DISTINCT source_product_id) AS observed_products
            FROM source_product_observations GROUP BY source_id) ob ON ob.source_id=s.id
        LEFT JOIN (SELECT source_id,COUNT(*) AS variant_offers FROM product_offers GROUP BY source_id) v ON v.source_id=s.id
        LEFT JOIN (SELECT source_id,COUNT(DISTINCT product_key) AS total_products FROM (
            SELECT store_id AS source_id,COALESCE(NULLIF(store_product_id,''),product_url) AS product_key FROM products
            UNION ALL SELECT source_id,source_product_id FROM source_product_observations
        ) GROUP BY source_id) t ON t.source_id=s.id
        ORDER BY s.wave,s.name,s.id''')]
