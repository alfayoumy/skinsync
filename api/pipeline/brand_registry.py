"""Explicit, curated aliases; string similarity never approves a brand identity."""
import re
import unicodedata

CURATED_BRANDS = {
    'CeraVe': ['cerave','cera ve','سيرافي','سيرافى'],
    'Avène': ['avene','avène','eau thermale avene','eau thermale avène'],
    'La Roche-Posay': ['la roche posay','lrp','la roche-posay laboratoire dermatologique','لاروش بوزيه'],
    'The Ordinary': ['the ordinary','ordinary','ذا اورديناري'],
    'SKIN1004': ['skin1004','skin 1004'],
    'Beauty of Joseon': ['beauty of joseon','boj'],
    'Bioderma': ['bioderma','بيوديرما'], 'COSRX':['cosrx'], 'NIVEA':['nivea'],
    'Garnier':['garnier'], 'Vichy':['vichy'], 'Anua':['anua'], 'PURITO':['purito','purito seoul'],
    'Round Lab':['round lab'], 'Eveline':['eveline','eveline cosmetics'], 'Dermacy':['dermacy','dermacy labs']
}


def brand_key(value):
    value = unicodedata.normalize('NFKC',value or '').casefold()
    return ' '.join(re.sub(r'[^\w\s]',' ',value).split())


def seed_brands(conn):
    for name, aliases in CURATED_BRANDS.items():
        conn.execute('INSERT OR IGNORE INTO brands(name) VALUES (?)',(name,))
        brand_id=conn.execute('SELECT id FROM brands WHERE name=?',(name,)).fetchone()[0]
        for alias in [name]+aliases:
            conn.execute('INSERT OR IGNORE INTO brand_aliases(alias,brand_id,approved_by) VALUES (?,?,?)',
                         (brand_key(alias),brand_id,'curated_registry_v1'))


def resolve_brand(value, conn=None):
    key=brand_key(value)
    if not key or key in ('generic','unknown','unbranded','none','n a'):
        return None
    if conn is not None:
        row=conn.execute('SELECT b.id,b.name FROM brand_aliases a JOIN brands b ON b.id=a.brand_id WHERE a.alias=?',(key,)).fetchone()
        if row:
            return {'id':row[0],'name':row[1],'key':brand_key(row[1]),'verified':True}
    for name, aliases in CURATED_BRANDS.items():
        if key in {brand_key(v) for v in [name]+aliases}:
            return {'id':None,'name':name,'key':brand_key(name),'verified':True}
    return {'id':None,'name':value.strip(),'key':key,'verified':False}


def approve_brand_alias(conn, alias, canonical_name, reviewer):
    if not reviewer.strip() or not brand_key(alias) or not canonical_name.strip():
        raise ValueError('Brand, alias and reviewer are required.')
    conn.execute('INSERT OR IGNORE INTO brands(name) VALUES (?)',(canonical_name,))
    brand_id=conn.execute('SELECT id FROM brands WHERE name=?',(canonical_name,)).fetchone()[0]
    existing=conn.execute('SELECT brand_id FROM brand_aliases WHERE alias=?',(brand_key(alias),)).fetchone()
    if existing and existing[0] != brand_id:
        raise ValueError('Alias is already assigned to a different verified brand.')
    conn.execute('INSERT OR IGNORE INTO brand_aliases(alias,brand_id,approved_by) VALUES (?,?,?)',(brand_key(alias),brand_id,reviewer))
    return brand_id


def register_legacy_brands(conn):
    """Preserve exact existing labels as separate brands, without fuzzy alias expansion."""
    names={r[0] for r in conn.execute('SELECT DISTINCT brand FROM canonical_products WHERE brand IS NOT NULL')}
    for name in sorted(names):
        resolved=resolve_brand(name,conn)
        if not resolved or resolved.get('id'):continue
        conn.execute("INSERT OR IGNORE INTO brands(name,status) VALUES (?,'legacy_exact')",(name,))
        bid=conn.execute('SELECT id FROM brands WHERE name=?',(name,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO brand_aliases(alias,brand_id,approved_by) VALUES (?,?,'legacy_exact_label')",(brand_key(name),bid))
    for row in conn.execute('SELECT id,brand FROM canonical_products WHERE brand_id IS NULL').fetchall():
        resolved=resolve_brand(row[1],conn)
        if resolved and resolved.get('id'):
            conn.execute('UPDATE canonical_products SET brand_id=? WHERE id=?',(resolved['id'],row[0]))
