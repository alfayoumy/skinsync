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
    'Round Lab':['round lab'], 'Eveline':['eveline','eveline cosmetics'], 'Dermacy':['dermacy','dermacy labs'],
    'ISISPHARMA':['isispharma','isis pharma'],
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
        row=conn.execute("""SELECT b.id,b.name,b.status,a.approved_by
            FROM brand_aliases a JOIN brands b ON b.id=a.brand_id WHERE a.alias=?""",(key,)).fetchone()
        if row:
            return {'id':row['id'],'name':row['name'],'key':brand_key(row['name']),
                    'verified':row['status'] not in ('observed_unverified',)
                      and row['approved_by']!='observed_source_unverified'}
    for name, aliases in CURATED_BRANDS.items():
        if key in {brand_key(v) for v in [name]+aliases}:
            return {'id':None,'name':name,'key':brand_key(name),'verified':True}
    return {'id':None,'name':value.strip(),'key':key,'verified':False}


def register_observed_brand(conn, value):
    """Create only an exact source-label brand anchor, NOT an approved alias.

    A previously unseen Shopify vendor is not a reason to hold a distinct
    product in identity review. Untrusted vendor labels remain explicitly
    unverified until a separate identity/brand-verification workflow approves
    them. No fuzzy normalization or cross-brand equivalence is inferred.
    """
    resolved=resolve_brand(value,conn)
    if not resolved or not resolved['name'].strip():
        return None
    if resolved.get('id'):
        return resolved
    key=brand_key(value)
    label=value.strip()
    if len(label)>160 or not key:
        return None
    # A curated label without an ID on a legacy schema is registered with
    # its known curated canonical name instead of duplicating a brand.
    canonical=resolved['name'] if resolved.get('verified') else label
    status='verified' if resolved.get('verified') else 'observed_unverified'
    conn.execute('INSERT OR IGNORE INTO brands(name,status) VALUES (?,?)',
                 (canonical,status))
    bid=conn.execute('SELECT id FROM brands WHERE name=?',(canonical,)).fetchone()[0]
    existing=conn.execute('SELECT brand_id FROM brand_aliases WHERE alias=?',(key,)).fetchone()
    if existing and existing[0]!=bid:
        return resolve_brand(value,conn)  # Existing curated identity wins.
    conn.execute('INSERT OR IGNORE INTO brand_aliases(alias,brand_id,approved_by) VALUES(?,?,?)',
                 (key,bid,'curated_registry_v1' if status=='verified' else 'observed_source_unverified'))
    return resolve_brand(value,conn)


def approve_brand_alias(conn, alias, canonical_name, reviewer):
    if not reviewer.strip() or not brand_key(alias) or not canonical_name.strip():
        raise ValueError('Brand, alias and reviewer are required.')
    conn.execute('INSERT OR IGNORE INTO brands(name) VALUES (?)',(canonical_name,))
    brand_id=conn.execute('SELECT id FROM brands WHERE name=?',(canonical_name,)).fetchone()[0]
    existing=conn.execute("""SELECT a.brand_id,a.approved_by,b.status
        FROM brand_aliases a JOIN brands b ON b.id=a.brand_id WHERE a.alias=?""",
        (brand_key(alias),)).fetchone()
    if existing and existing['brand_id'] != brand_id:
        if (existing['status']!='observed_unverified'
                or existing['approved_by']!='observed_source_unverified'):
            raise ValueError('Alias is already assigned to a different verified brand.')
        # A curator may correct a previously unapproved source *label*.
        # Do not auto-merge canonicals created under that source label;
        # those products still need an explicit audited identity operation.
        conn.execute("""UPDATE brand_aliases SET brand_id=?,approved_by=?
            WHERE alias=? AND brand_id=?""",
            (brand_id,reviewer,brand_key(alias),existing['brand_id']))
    else:
        conn.execute('INSERT OR IGNORE INTO brand_aliases(alias,brand_id,approved_by) VALUES (?,?,?)',
                     (brand_key(alias),brand_id,reviewer))
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


def consolidate_brand_labels(conn,canonical_name,aliases,reviewer,reason,evidence_urls):
    """Explicitly consolidate reviewed brand labels without merging products.

    Unlike alias approval this may retire an existing legacy brand ID. Every
    affected alias and canonical brand reference is retained in an audit.
    """
    import json
    from urllib.parse import urlparse
    if not reviewer or not reviewer.strip() or not reason or not reason.strip():
        raise ValueError('Reviewer and evidence explanation are required.')
    if not evidence_urls or any(urlparse(u).scheme!='https' or not urlparse(u).hostname for u in evidence_urls):
        raise ValueError('Brand consolidation requires HTTPS evidence URLs.')
    target=conn.execute('SELECT id FROM brands WHERE name=?',(canonical_name,)).fetchone()
    if not target:raise ValueError('Choose an existing canonical brand.')
    target=target[0]
    keys=sorted({brand_key(a) for a in [canonical_name,*aliases] if brand_key(a)})
    if not keys:raise ValueError('Brand aliases are required.')
    marks=','.join('?' for _ in keys)
    labels=[dict(r) for r in conn.execute(f'SELECT * FROM brand_aliases WHERE alias IN ({marks})',keys)]
    old_ids=sorted({r['brand_id'] for r in labels}-{target})
    # Consolidate whole IDs only when all of their aliases were explicitly
    # reviewed; do not silently transfer unrelated aliases from a larger brand.
    for bid in old_ids:
        if any(r[0] not in keys for r in conn.execute('SELECT alias FROM brand_aliases WHERE brand_id=?',(bid,))):
            raise ValueError('Every alias of a retired brand must be reviewed explicitly.')
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT brand_consolidation')
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS brand_identity_history(
            id INTEGER PRIMARY KEY,canonical_brand_id INTEGER NOT NULL,
            before_state TEXT NOT NULL,after_state TEXT NOT NULL,
            evidence_urls TEXT NOT NULL,reviewed_by TEXT NOT NULL,reason TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        ids=[target,*old_ids];slots=','.join('?' for _ in ids)
        def snapshot():
            return {'brands':[dict(r) for r in conn.execute(f'SELECT * FROM brands WHERE id IN ({slots})',ids)],
                    'aliases':[dict(r) for r in conn.execute(f'SELECT * FROM brand_aliases WHERE brand_id IN ({slots})',ids)],
                    'canonicals':[dict(r) for r in conn.execute(f'SELECT id,brand_id,brand FROM canonical_products WHERE brand_id IN ({slots})',ids)]}
        before=snapshot()
        for key in keys:
            conn.execute('''INSERT INTO brand_aliases(alias,brand_id,approved_by) VALUES(?,?,?)
                ON CONFLICT(alias) DO UPDATE SET brand_id=excluded.brand_id,approved_by=excluded.approved_by''',(key,target,reviewer))
        for bid in old_ids:
            conn.execute('UPDATE canonical_products SET brand_id=?,updated_at=CURRENT_TIMESTAMP WHERE brand_id=?',(target,bid))
            conn.execute("UPDATE brands SET status='consolidated' WHERE id=?",(bid,))
        conn.execute("UPDATE brands SET status='verified' WHERE id=?",(target,))
        history=conn.execute('''INSERT INTO brand_identity_history(canonical_brand_id,before_state,after_state,evidence_urls,reviewed_by,reason)
            VALUES(?,?,?,?,?,?)''',(target,json.dumps(before),json.dumps(snapshot()),json.dumps(evidence_urls),reviewer,reason)).lastrowid
        conn.execute('RELEASE SAVEPOINT brand_consolidation')
        return {'brand_id':target,'retired_brand_ids':old_ids,'history_id':history}
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT brand_consolidation');conn.execute('RELEASE SAVEPOINT brand_consolidation');raise
