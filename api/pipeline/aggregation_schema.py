"""Additive master-data schema; never rebuilds or renumbers legacy tables."""
import json

TABLES = [
'''CREATE TABLE IF NOT EXISTS brands(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'verified')''',
'''CREATE TABLE IF NOT EXISTS brand_aliases(alias TEXT PRIMARY KEY, brand_id INTEGER NOT NULL REFERENCES brands(id), approved_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
'''CREATE TABLE IF NOT EXISTS product_taxonomy(id TEXT PRIMARY KEY, label TEXT NOT NULL, vertical TEXT NOT NULL, parent_id TEXT, is_skincare INTEGER NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,domain TEXT NOT NULL UNIQUE,
 source_type TEXT NOT NULL CHECK(source_type IN ('retailer','manufacturer','reference')),
 adapter_type TEXT NOT NULL,market TEXT NOT NULL,language TEXT NOT NULL DEFAULT 'en',
 provides_prices INTEGER NOT NULL DEFAULT 0,provides_inci INTEGER NOT NULL DEFAULT 1,
 reliability TEXT NOT NULL DEFAULT 'unvalidated',wave INTEGER NOT NULL DEFAULT 1,
 enabled INTEGER NOT NULL DEFAULT 0,publish_enabled INTEGER NOT NULL DEFAULT 0,
 section_rules TEXT NOT NULL DEFAULT '{}',rate_limit_seconds REAL NOT NULL DEFAULT 1,
 last_successful_sync TEXT)''',
'''CREATE TABLE IF NOT EXISTS source_product_observations(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),
 source_product_id TEXT NOT NULL,source_url TEXT NOT NULL,raw_title TEXT NOT NULL,raw_brand TEXT,raw_category TEXT,
 source_gtin TEXT,manufacturer_sku TEXT,description TEXT,images TEXT NOT NULL DEFAULT '[]',
 ingredient_text TEXT,ingredient_section_type TEXT,market TEXT,language TEXT,
 observed_at TEXT NOT NULL,payload_hash TEXT NOT NULL,raw_payload TEXT NOT NULL,
 identity_attributes TEXT NOT NULL,classification TEXT NOT NULL,
 UNIQUE(source_id,source_product_id,payload_hash))''',
'''CREATE INDEX IF NOT EXISTS idx_observations_source_product ON source_product_observations(source_id,source_product_id,id)''',
'''CREATE TABLE IF NOT EXISTS source_product_variants(id INTEGER PRIMARY KEY,observation_id INTEGER NOT NULL REFERENCES source_product_observations(id),
 source_variant_id TEXT NOT NULL,sku TEXT,gtin TEXT,title TEXT,attributes TEXT NOT NULL,UNIQUE(observation_id,source_variant_id))''',
'''CREATE TABLE IF NOT EXISTS product_identity_links(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,source_product_id TEXT NOT NULL,
 canonical_product_id INTEGER REFERENCES canonical_products(id),status TEXT NOT NULL CHECK(status IN ('accepted','rejected','pending')),
 method TEXT NOT NULL,evidence TEXT NOT NULL,reviewed_by TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source_id,source_product_id,canonical_product_id))''',
'''CREATE TABLE IF NOT EXISTS canonical_product_aliases(id INTEGER PRIMARY KEY,canonical_product_id INTEGER NOT NULL REFERENCES canonical_products(id),
 alias_type TEXT NOT NULL,alias_value TEXT NOT NULL,source_id TEXT NOT NULL DEFAULT '',verified INTEGER NOT NULL DEFAULT 0,
 UNIQUE(canonical_product_id,alias_type,alias_value,source_id))''',
'''CREATE INDEX IF NOT EXISTS idx_canonical_alias_lookup ON canonical_product_aliases(alias_type,alias_value)''',
'''CREATE TABLE IF NOT EXISTS canonical_identity_constraints(left_id INTEGER NOT NULL,right_id INTEGER NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('cannot_link','same_identity')),reviewed_by TEXT NOT NULL,reason TEXT NOT NULL,
 PRIMARY KEY(left_id,right_id),CHECK(left_id < right_id))''',
'''CREATE TABLE IF NOT EXISTS canonical_identity_history(id INTEGER PRIMARY KEY,operation TEXT NOT NULL,
 canonical_product_id INTEGER NOT NULL,target_id INTEGER,before_state TEXT NOT NULL,after_state TEXT NOT NULL,
 reviewed_by TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,reverses_id INTEGER)''',
'''CREATE TABLE IF NOT EXISTS canonical_redirects(canonical_product_id INTEGER PRIMARY KEY,target_id INTEGER NOT NULL,
 history_id INTEGER NOT NULL REFERENCES canonical_identity_history(id))''',
'''CREATE TABLE IF NOT EXISTS identity_gemini_queue(
 review_id INTEGER PRIMARY KEY REFERENCES product_review_queue(id),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','auto_resolved','needs_manual')),
 evidence_hash TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 confidence REAL,
 verdict TEXT,
 explanation TEXT,
 model_name TEXT,
 task_id TEXT,
 queued_by TEXT NOT NULL,
 queued_at TEXT DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
'''CREATE INDEX IF NOT EXISTS idx_identity_gemini_status ON identity_gemini_queue(status,review_id)''',
'''CREATE TABLE IF NOT EXISTS product_review_queue(id INTEGER PRIMARY KEY,case_key TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
 source_id TEXT,source_product_id TEXT,canonical_product_id INTEGER,details TEXT NOT NULL,evidence_hash TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',decision TEXT,reviewed_by TEXT,review_notes TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
'''CREATE TABLE IF NOT EXISTS product_offers(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),source_product_id TEXT NOT NULL,
 source_variant_id TEXT NOT NULL,canonical_product_id INTEGER NOT NULL REFERENCES canonical_products(id),
 legacy_product_id INTEGER REFERENCES products(id),observation_id INTEGER REFERENCES source_product_observations(id),
 product_url TEXT NOT NULL,title TEXT NOT NULL,sku TEXT,gtin TEXT,price REAL NOT NULL DEFAULT 0,currency TEXT NOT NULL DEFAULT 'EGP',
 compare_at_price REAL NOT NULL DEFAULT 0,is_available INTEGER NOT NULL DEFAULT 0,image_url TEXT,
 pack_count INTEGER NOT NULL DEFAULT 1,unit_size REAL,unit_size_unit TEXT,total_quantity REAL,
 normalized_size_ml REAL,normalized_size_g REAL,unit_price REAL,unit_measure TEXT,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(source_id,source_product_id,source_variant_id))''',
'''CREATE INDEX IF NOT EXISTS idx_product_offers_canonical ON product_offers(canonical_product_id)''',
'''CREATE INDEX IF NOT EXISTS idx_product_offers_gtin ON product_offers(gtin)''',
'''CREATE TABLE IF NOT EXISTS formulation_versions(id INTEGER PRIMARY KEY,canonical_product_id INTEGER NOT NULL REFERENCES canonical_products(id),
 formula_version INTEGER NOT NULL,formula_hash TEXT,raw_ingredients TEXT NOT NULL,parsed_tokens TEXT NOT NULL,
 source_name TEXT,source_url TEXT,approval_reason TEXT,effective_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(canonical_product_id,formula_version))''',
'''CREATE TABLE IF NOT EXISTS formulation_resolution_queue(canonical_product_id INTEGER PRIMARY KEY REFERENCES canonical_products(id),
 status TEXT NOT NULL DEFAULT 'pending',updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
'''CREATE TABLE IF NOT EXISTS aggregation_rollouts(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),
 report_hash TEXT NOT NULL,reviewed_by TEXT NOT NULL,notes TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
'''CREATE TABLE IF NOT EXISTS legacy_identity_mapping(product_id INTEGER PRIMARY KEY,canonical_product_id INTEGER,
 captured_at TEXT DEFAULT CURRENT_TIMESTAMP)''',
]


def ensure_identity_gemini_schema(conn):
    """Idempotently migrate an existing catalog on first Gemini review access.

    Existing installations may start the API without re-running init_db(). A
    review listing must remain available when that optional queue table has
    not been created yet.
    """
    for sql in TABLES:
        if (sql.startswith('CREATE TABLE IF NOT EXISTS identity_gemini_queue(')
                or sql.startswith('CREATE INDEX IF NOT EXISTS idx_identity_gemini_status')):
            conn.execute(sql)


def init_aggregation_schema(conn):
    for sql in TABLES:
        conn.execute(sql)
    additions = {
        'canonical_products': [('brand_id','INTEGER REFERENCES brands(id)'),('identity_attributes',"TEXT NOT NULL DEFAULT '{}'"),
            ('identity_status',"TEXT NOT NULL DEFAULT 'legacy'"),('classification',"TEXT NOT NULL DEFAULT '{}'"),
            ('classification_status',"TEXT NOT NULL DEFAULT 'legacy'"),('catalog_visible','INTEGER NOT NULL DEFAULT 1'),
            ('family_name','TEXT'),('market','TEXT'),('formula_region','TEXT'),('formula_revision','TEXT'),('formulation_provenance',"TEXT NOT NULL DEFAULT 'legacy_unverified'")],
        'product_identity_links': [('identity_status',"TEXT NOT NULL DEFAULT 'legacy_preserved_unverified'")],
        'products': [('original_title','TEXT'),('original_brand','TEXT'),('original_category','TEXT'),
            ('classification',"TEXT NOT NULL DEFAULT '{}'"),('brand_id','INTEGER REFERENCES brands(id)')],
        'inci_audit_evidence': [('observation_id','INTEGER REFERENCES source_product_observations(id)'),
            ('source_market','TEXT'),('formula_code','TEXT'),('order_verified','INTEGER')]
    }
    for table, fields in additions.items():
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not columns: continue
        for name, definition in fields:
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    conn.execute('INSERT OR IGNORE INTO legacy_identity_mapping(product_id,canonical_product_id) SELECT id,canonical_product_id FROM products')

    conn.execute('''CREATE TABLE IF NOT EXISTS source_evaluations(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),
        samples TEXT NOT NULL,baseline TEXT NOT NULL,created_by TEXT NOT NULL,policy_hash TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    if 'policy_hash' not in {r[1] for r in conn.execute('PRAGMA table_info(source_evaluations)')}:
        conn.execute("ALTER TABLE source_evaluations ADD COLUMN policy_hash TEXT NOT NULL DEFAULT ''")
    conn.execute('''CREATE TABLE IF NOT EXISTS source_evaluation_labels(run_id INTEGER NOT NULL REFERENCES source_evaluations(id),
        observation_id INTEGER NOT NULL,expected_canonical_id INTEGER,expected_new INTEGER NOT NULL,
        category TEXT NOT NULL,is_skincare INTEGER NOT NULL,reviewed_by TEXT NOT NULL,reason TEXT NOT NULL,
        PRIMARY KEY(run_id,observation_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS source_evaluation_results(run_id INTEGER PRIMARY KEY REFERENCES source_evaluations(id),
        report TEXT NOT NULL,report_hash TEXT NOT NULL,finalized_by TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconciliation_previews(id INTEGER PRIMARY KEY,baseline_hash TEXT NOT NULL,
        report TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    for table in ('source_evaluations','source_evaluation_labels','source_evaluation_results','reconciliation_previews'):
        for action in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Evaluation and preview artifacts are immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS evaluation_labels_closed BEFORE INSERT ON source_evaluation_labels WHEN EXISTS (SELECT 1 FROM source_evaluation_results WHERE run_id=NEW.run_id) BEGIN SELECT RAISE(ABORT,'Evaluation already finalized'); END")
