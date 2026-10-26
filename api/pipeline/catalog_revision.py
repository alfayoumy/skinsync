"""Track committed changes that affect the public catalog memory index."""


def ensure_catalog_revision_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS catalog_revision(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL DEFAULT 0)')
    conn.execute('INSERT OR IGNORE INTO catalog_revision(singleton,revision) VALUES(1,0)')
    watches={
        'canonical_products':('canonical_title','brand','category','is_skincare','catalog_visible',
            'ingredients_raw','ingredient_source','min_price','max_price','store_count','primary_image_url',
            'gate4_jev_status','gate4_gemini_status','gate4_cross_status'),
        'products':('canonical_product_id','store_id','store_product_id','image_url','product_url','is_available','price'),
        'product_offers':('canonical_product_id','source_id','source_product_id','product_url','is_available','legacy_product_id','price','currency','image_url','identity_link_product_id'),
        'product_identity_links':('canonical_product_id','source_id','source_product_id','status','identity_status'),
        'sources':('provides_prices','wave','publish_enabled'),
    }
    canonical_columns={r[1] for r in conn.execute('PRAGMA table_info(canonical_products)')}
    def visible(prefix):
        return f'{prefix}.is_skincare=1 AND {prefix}.catalog_visible=1' if {'is_skincare','catalog_visible'}<=canonical_columns else '1'
    def affected(table,prefix):
        if table=='canonical_products':return '('+visible(prefix)+')'
        if table in ('products','product_offers','product_identity_links'):
            return f'EXISTS(SELECT 1 FROM canonical_products c WHERE c.id={prefix}.canonical_product_id AND {visible("c")})'
        return '1'
    for table,fields in watches.items():
        columns={r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not columns:continue
        watched=[f for f in fields if f in columns]
        changed=' OR '.join(f'OLD.{f} IS NOT NEW.{f}' for f in watched) or '0'
        conditions={'INSERT':affected(table,'NEW'),'DELETE':affected(table,'OLD'),
                    'UPDATE':f'({changed}) AND ({affected(table,"OLD")} OR {affected(table,"NEW")})'}
        for action,condition in conditions.items():
            conn.execute(f'DROP TRIGGER IF EXISTS catalog_revision_v1_{table}_{action.lower()}')
            conn.execute(f'DROP TRIGGER IF EXISTS catalog_revision_v2_{table}_{action.lower()}')
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS catalog_revision_v3_{table}_{action.lower()}
                AFTER {action} ON {table} WHEN {condition}
                BEGIN UPDATE catalog_revision SET revision=revision+1 WHERE singleton=1; END''')
