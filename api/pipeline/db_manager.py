import sqlite3
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("skincare_pipeline.db")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skincare.db")

def get_connection(db_path=DB_PATH):
    """
    Returns a connection to the SQLite database with WAL mode and row factory enabled.
    """
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging for high-concurrency read/write
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# Default authoritative seeds for ingredient synonyms
DEFAULT_SYNONYMS = [
    # Carriers / Solvents
    ('aqua', 'water', 'carrier'),
    ('eau', 'water', 'carrier'),
    ('purified water', 'water', 'carrier'),
    ('deionized water', 'water', 'carrier'),

    # Fragrances / Sensitizers
    ('parfum', 'fragrance', 'fragrance'),
    ('perfume', 'fragrance', 'fragrance'),
    ('fragrance (parfum)', 'fragrance', 'fragrance'),

    # Alcohols
    ('alcohol denat.', 'alcohol denat', 'alcohol'),
    ('alcohol denat', 'alcohol denat', 'alcohol'),
    ('denatured alcohol', 'alcohol denat', 'alcohol'),

    # Surfactants / Cleansers
    ('sodium lauryl sulfate', 'sls', 'surfactant'),
    ('sodium laureth sulfate', 'sles', 'surfactant'),

    # Oils & Emollients
    ('cocos nucifera (coconut) oil', 'coconut oil', 'oil'),
    ('cocos nucifera oil', 'coconut oil', 'oil'),
    ('coconut oil', 'coconut oil', 'oil'),
    ('theobroma cacao (cocoa) seed butter', 'cocoa butter', 'butter'),
    ('theobroma cacao seed butter', 'cocoa butter', 'butter'),
    ('cocoa butter', 'cocoa butter', 'butter'),
    ('butyrospermum parkii (shea) butter', 'shea butter', 'butter'),
    ('butyrospermum parkii butter', 'shea butter', 'butter'),
    ('shea butter', 'shea butter', 'butter'),
    ('simmondsia chinensis (jojoba) seed oil', 'jojoba oil', 'oil'),
    ('simmondsia chinensis seed oil', 'jojoba oil', 'oil'),
    ('jojoba oil', 'jojoba oil', 'oil'),
    ('tea tree oil', 'tea tree oil', 'oil'),
    ('tea tree', 'tea tree oil', 'oil'),
    ('melaleuca alternifolia', 'tea tree oil', 'oil'),
    ('melaleuca alternifolia (tea tree) leaf oil', 'tea tree oil', 'oil'),

    # Actives / Soothing
    ('aloe barbadensis', 'aloe vera', 'botanical'),
    ('aloe barbadensis leaf juice', 'aloe vera', 'botanical'),
    ('aloe vera', 'aloe vera', 'botanical'),
    ('centella asiatica', 'centella', 'botanical'),
    ('centella asiatica extract', 'centella', 'botanical'),
    ('cica', 'centella', 'botanical'),
    ('madecassoside', 'centella', 'botanical'),
    ('panthenol', 'panthenol', 'active'),
    ('d-panthenol', 'panthenol', 'active'),
    ('pro-vitamin b5', 'panthenol', 'active'),
    ('provitamin b5', 'panthenol', 'active'),
    ('vitamin b5', 'panthenol', 'active'),
    ('niacinamide', 'niacinamide', 'active'),
    ('nicotinamide', 'niacinamide', 'active'),
    ('vitamin b3', 'niacinamide', 'active'),
    ('ascorbic acid', 'l-ascorbic acid', 'active'),
    ('l-ascorbic acid', 'l-ascorbic acid', 'active'),
    ('vitamin c', 'l-ascorbic acid', 'active'),
    ('tocopherol', 'tocopherol', 'active'),
    ('vitamin e', 'tocopherol', 'active'),
    ('salicylic acid', 'salicylic acid', 'active'),
    ('bha', 'salicylic acid', 'active'),
    ('aha', 'glycolic acid', 'active'),
    ('glycolic acid', 'glycolic acid', 'active'),
    ('lactic acid', 'lactic acid', 'active'),
    ('sodium hyaluronate', 'hyaluronic acid', 'active'),
    ('hyaluronate', 'hyaluronic acid', 'active'),
    ('hyaluronic acid', 'hyaluronic acid', 'active'),
    ('retinol', 'retinol', 'active'),
    ('retinyl palmitate', 'retinyl palmitate', 'active')
]

def init_db(db_path=DB_PATH):
    """
    Initializes database schema with proper indexes for fast searches and historical tracking.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()

    # 1. Stores Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stores (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        domain TEXT NOT NULL UNIQUE,
        platform TEXT NOT NULL DEFAULT 'shopify',
        is_active INTEGER NOT NULL DEFAULT 1,
        last_sync_at TIMESTAMP,
        total_products INTEGER DEFAULT 0
    );
    """)

    # 2. Products Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        store_product_id TEXT,
        handle TEXT,
        title TEXT NOT NULL,
        clean_title TEXT,
        brand TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL DEFAULT 0.0,
        compare_at_price REAL DEFAULT 0.0,
        is_available INTEGER NOT NULL DEFAULT 1,
        image_url TEXT,
        product_url TEXT NOT NULL UNIQUE,
        ingredients_raw TEXT,
        ingredients_parsed TEXT, -- JSON array of normalized tokens
        actives TEXT,            -- JSON array of key actives
        ingredient_source TEXT,  -- 'store_description', 'skinsafe_matched', 'curated'
        inci_matched_id INTEGER,
        inci_matched_name TEXT,
        inci_vector_score REAL,
        inci_token_score REAL,
        inci_hybrid_score REAL,
        inci_cross_score REAL,
        inci_jev_score REAL,
        inci_gemini_score REAL,
        inci_safeguard_log TEXT,
        gate1_quality_status TEXT,
        gate2_safeguard_status TEXT,
        gate3_active_status TEXT,
        gate4_cross_status TEXT,
        gate4_jev_status TEXT,
        gate4_gemini_status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (store_id) REFERENCES stores(id)
    );
    """)

    # Ensure all telemetry columns exist in existing databases
    cur.execute("PRAGMA table_info(products);")
    existing_cols = {r[1] for r in cur.fetchall()}
    telemetry_cols = [
        ('clean_title', 'TEXT'),
        ('inci_matched_id', 'INTEGER'),
        ('inci_matched_name', 'TEXT'),
        ('inci_vector_score', 'REAL'),
        ('inci_token_score', 'REAL'),
        ('inci_hybrid_score', 'REAL'),
        ('inci_cross_score', 'REAL'),
        ('inci_jev_score', 'REAL'),
        ('inci_gemini_score', 'REAL'),
        ('inci_safeguard_log', 'TEXT'),
        ('gate1_quality_status', 'TEXT'),
        ('gate2_safeguard_status', 'TEXT'),
        ('gate3_active_status', 'TEXT'),
        ('gate4_cross_status', 'TEXT'),
        ('gate4_jev_status', 'TEXT'),
        ('gate4_gemini_status', 'TEXT')
    ]
    for col_name, col_type in telemetry_cols:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type};")

    # 3. Price History Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        old_price REAL NOT NULL,
        new_price REAL NOT NULL,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );
    """)

    # 4. INCI Knowledge Base (SkinSAFE & Curated)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inci_reference (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand TEXT NOT NULL,
        product_name TEXT NOT NULL,
        ingredients TEXT NOT NULL,
        source TEXT DEFAULT 'skinsafe'
    );
    """)

    # 5. Pipeline Sync Log
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        status TEXT NOT NULL, -- 'running', 'success', 'failed'
        products_fetched INTEGER DEFAULT 0,
        products_added INTEGER DEFAULT 0,
        products_updated INTEGER DEFAULT 0,
        price_changes INTEGER DEFAULT 0,
        error_message TEXT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    );
    """)

    # 6. Persistent API Rate Limiting & Usage Tracking (RPM / RPD)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_rate_limits (
        provider TEXT NOT NULL,
        usage_date TEXT NOT NULL, -- YYYY-MM-DD UTC
        request_count INTEGER NOT NULL DEFAULT 0,
        last_request_ts REAL NOT NULL DEFAULT 0.0,
        PRIMARY KEY (provider, usage_date)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_request_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        ts REAL NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_api_request_log ON api_request_log(provider, ts);")

    # 7. INCI Ingredient Synonyms & Normalization Table (Database-backed)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ingredient_synonyms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alias TEXT NOT NULL UNIQUE,          -- lowercased synonym / alias (e.g. 'aqua', 'sls', 'fragrance')
        canonical_name TEXT NOT NULL,         -- normalized target name (e.g. 'water', 'Sodium Lauryl Sulfate')
        category TEXT DEFAULT 'general',     -- 'carrier', 'fragrance', 'surfactant', 'emollient', 'active', etc.
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingredient_synonyms_alias ON ingredient_synonyms(alias);")

    # Indexes for sub-millisecond query performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_url ON products(product_url);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_price_history_pid ON price_history(product_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inci_ref_brand ON inci_reference(brand);")

    # Seed Default Stores
    default_stores = [
        ('sourcebeauty', 'Source Beauty', 'sourcebeauty.com', 'shopify'),
        ('feel22', 'Feel22 Egypt', 'eg.feel22.com', 'shopify'),
        ('bloompharmacy', 'Bloom Pharmacy', 'www.bloompharmacy.com', 'shopify'),
        ('drdawood', 'Dr Dawood Pharmacy', 'drdawoodstore.com', 'shopify'),
        ('bashrety', 'Bashrety', 'bashrety.tv', 'shopify'),
        ('rawafrican', 'Raw African', 'rawafrican.net', 'shopify')
    ]

    for s_id, s_name, s_dom, s_plat in default_stores:
        cur.execute("""
        INSERT OR IGNORE INTO stores (id, name, domain, platform)
        VALUES (?, ?, ?, ?);
        """, (s_id, s_name, s_dom, s_plat))

    # Seed Default Synonyms
    for alias, canon, cat in DEFAULT_SYNONYMS:
        cur.execute("""
        INSERT INTO ingredient_synonyms (alias, canonical_name, category)
        VALUES (?, ?, ?)
        ON CONFLICT(alias) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            category = excluded.category;
        """, (alias.lower().strip(), canon.strip(), cat))

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully at %s", db_path)

def load_synonyms_from_db(conn=None, db_path=DB_PATH) -> dict:
    """
    Loads all ingredient synonyms from the database into a dictionary.
    Returns: {alias: canonical_name}
    """
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT alias, canonical_name FROM ingredient_synonyms;")
        rows = cur.fetchall()
        if not rows:
            return {a: c for a, c, _ in DEFAULT_SYNONYMS}
        return {r[0].lower().strip(): r[1].strip() for r in rows}
    except Exception as e:
        logger.warning(f"Failed to load synonyms from database: {e}")
        return {a: c for a, c, _ in DEFAULT_SYNONYMS}
    finally:
        if should_close:
            conn.close()

def add_synonym_to_db(alias: str, canonical_name: str, category: str = 'general', conn=None, db_path=DB_PATH) -> bool:
    """Inserts or updates a synonym in the database."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO ingredient_synonyms (alias, canonical_name, category)
        VALUES (?, ?, ?)
        ON CONFLICT(alias) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            category = excluded.category;
        """, (alias.lower().strip(), canonical_name.strip(), category.strip()))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to save synonym '{alias}': {e}")
        return False
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database schema created successfully.")
