import sqlite3
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

try:
    from pipeline.logging_config import setup_logging, get_logger
    logger = get_logger("skincare_pipeline.db")
except ImportError:
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
    conn.execute("PRAGMA busy_timeout = 60000;")
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

DEFAULT_CLINICAL_INGREDIENTS = [
    ('Cocos Nucifera (Coconut) Oil', 'Coconut oil', 'oil', 4, 1, ['moisturizer', 'occlusive'],
     'Widely reported high comedogenic rating; tends to clog pores in acne-prone skin at leave-on concentrations.', [],
     'Effective emollient for dry skin; avoid in acne-prone/oily skin when used as primary leave-on oil.'),
    ('Theobroma Cacao (Cocoa) Seed Butter', 'Cocoa butter', 'butter', 4, 1, ['moisturizer'], '', [],
     'Rich occlusive; useful for very dry skin but can be pore-clogging in acne-prone skin.'),
    ('Isopropyl Myristate', 'Isopropyl myristate', 'emollient', 5, 2, ['texture', 'emollient'],
     'Classic comedogenicity tables flag IPM/IPMa as high risk, particularly in leave-on formulations.', [],
     'Often used to increase spreadability; avoid in acne-prone formulations or keep at low concentrations in rinse-off.'),
    ('Isopropyl Palmitate', 'Isopropyl palmitate', 'emollient', 4, 1, ['texture'], '', [], ''),
    ('Ethylhexyl Palmitate', 'Ethylhexyl palmitate', 'emollient', 4, 1, ['texture'], '', [], ''),
    ('Sodium Laureth Sulfate', 'SLES', 'surfactant', 0, 2, ['cleanser'], '', [],
     'Less irritating than SLS for many users, but still can be drying/irritating depending on formulation.'),
    ('Sodium Lauryl Sulfate', 'SLS', 'surfactant', 0, 5, ['cleanser', 'irritant'],
     'SLS is a well-documented irritant in patch/wash models. Avoid in sensitive-skin-targeted cleansers.', [],
     'Primarily an irritant; not a comedogen per se.'),
    ('Alcohol Denat.', 'Denatured alcohol', 'solvent', 0, 3, ['drying'],
     'Denatured alcohol can be drying and can exacerbate barrier dysfunction in sensitive/dry skin.', [],
     'Short-term texture benefits; avoid as primary active in dry/sensitive-skin leave-on products.'),
    ('Parfum', 'Fragrance (generic)', 'fragrance', 0, 4, ['fragrance'],
     'Fragrance is a leading cause of allergic contact dermatitis from cosmetics; many fragrance components are sensitizers.', [],
     'Use fragrance-free labeling for sensitive-skin products.'),
    ('Menthol', 'Menthol', 'sensitizer', 0, 5, ['cooling', 'sensitizer'], '', [], 'Common irritant and sensitizer in leave-on products.'),
    ('Melaleuca Alternifolia (Tea Tree) Leaf Oil', 'Tea tree oil', 'essential_oil', 1, 2, ['antimicrobial', 'acne_fighting'], '', [],
     'Has antimicrobial properties but can be irritating/sensitizing to some users; concentration-dependent.'),
    ('Niacinamide', 'Niacinamide (Vitamin B3)', 'active', 0, 1, ['brightening', 'acne_fighting', 'barrier_repair'],
     'Clinical testing shows good tolerability up to commonly used concentrations.', [], 'Generally well tolerated.'),
    ('Salicylic Acid', 'Salicylic acid (BHA)', 'exfoliant', 0, 2, ['acne_fighting', 'bha'],
     'Proven efficacy for acne; formulations may cause dryness/irritation depending on pH and vehicle.', [], ''),
    ('Glycolic Acid', 'Glycolic acid (AHA)', 'exfoliant', 0, 3, ['anti_aging', 'aha'], '', [],
     'Irritancy depends on concentration/pH; leave-on high-strength AHAs can be sensitizing.'),
    ('Retinol', 'Retinol (vitamin A derivative)', 'retinoid', 0, 4, ['anti_aging', 'acne_fighting'],
     'Topical retinoids increase skin turnover and can irritate; topical retinoids are generally advised against in pregnancy.',
     ['pregnancy'], 'Introduce slowly; use sunscreen due to photosensitivity.'),
    ('Ascorbic Acid', 'L-ascorbic acid (Vitamin C)', 'active', 0, 3, ['brightening', 'antioxidant'], '', [],
     'Potent antioxidant but acidic and can irritate sensitive skin; stability issues require appropriate formulation.'),
    ('Sodium Ascorbyl Phosphate', 'Sodium ascorbyl phosphate (SAP)', 'active', 0, 1, ['brightening', 'vitamin_c'],
     'SAP is a more stable, gentler vitamin C derivative often used for sensitive or acne-prone skin.', [],
     'Less irritating than L-ascorbic acid in many formulations.'),
    ('Sodium Hyaluronate', 'Sodium hyaluronate', 'humectant', 0, 0, ['hydration'], '', [], ''),
    ('Hyaluronic Acid', 'Hyaluronic acid', 'humectant', 0, 0, ['hydration'], '', [], ''),
    ('Glycerin', 'Glycerin', 'humectant', 0, 0, ['hydration'], '', [], ''),
    ('Ceramide NP', 'Ceramide NP', 'lipid', 0, 0, ['barrier_repair'], '', [], ''),
    ('Panthenol', 'Panthenol (pro-vitamin B5)', 'humectant', 0, 0, ['soothing'], '', [], ''),
    ('Allantoin', 'Allantoin', 'soother', 0, 0, ['soothing'], '', [], ''),
    ('Centella Asiatica Extract', 'Centella asiatica', 'extract', 0, 0, ['soothing'], '', [], ''),
    ('Aloe Barbadensis Leaf Juice', 'Aloe vera', 'extract', 0, 0, ['soothing'], '', [], ''),
    ('Squalane', 'Squalane (plant/synthetic)', 'oil', 1, 0, ['moisturizer'], '', [],
     'Generally low comedogenic risk and well tolerated in many skin types.'),
    ('Simmondsia Chinensis (Jojoba) Seed Oil', 'Jojoba oil', 'oil', 2, 0, ['moisturizer'], '', [], ''),
    ('Butyrospermum Parkii (Shea) Butter', 'Shea butter', 'butter', 2, 0, ['moisturizer'], '', [],
     'Useful for dry skin; may be heavier for oily/acne-prone skin depending on formula.'),
    ('Phenoxyethanol', 'Phenoxyethanol', 'preservative', 0, 1, ['preservative'], '', [], ''),
    ('Parabens', 'Parabens (methylparaben, propylparaben etc.)', 'preservative', 0, 2, ['preservative'], '', [], ''),
    ('Tocopherol', 'Vitamin E (tocopherol)', 'antioxidant', 2, 1, ['vitamin_e'], '', [], '')
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
        description_html TEXT,
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
        canonical_product_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (store_id) REFERENCES stores(id)
    );
    """)

    # Ensure all telemetry columns exist in existing databases
    cur.execute("PRAGMA table_info(products);")
    existing_cols = {r[1] for r in cur.fetchall()}
    telemetry_cols = [
        ('description_html', 'TEXT'),
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
        ('gate4_gemini_status', 'TEXT'),
        ('canonical_product_id', 'INTEGER'),
        ('tier_resolutions', 'TEXT')
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
        token_count INTEGER NOT NULL DEFAULT 0,
        is_exhausted INTEGER NOT NULL DEFAULT 0,
        exhausted_until REAL NOT NULL DEFAULT 0.0,
        PRIMARY KEY (provider, usage_date)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_request_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        ts REAL NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_api_request_log ON api_request_log(provider, ts);")

    # Safe migrations for rate limit tracking tables
    cur.execute("PRAGMA table_info(api_rate_limits);")
    rate_cols = {r[1] for r in cur.fetchall()}
    if "token_count" not in rate_cols:
        cur.execute("ALTER TABLE api_rate_limits ADD COLUMN token_count INTEGER NOT NULL DEFAULT 0;")
    if "is_exhausted" not in rate_cols:
        cur.execute("ALTER TABLE api_rate_limits ADD COLUMN is_exhausted INTEGER NOT NULL DEFAULT 0;")
    if "exhausted_until" not in rate_cols:
        cur.execute("ALTER TABLE api_rate_limits ADD COLUMN exhausted_until REAL NOT NULL DEFAULT 0.0;")

    cur.execute("PRAGMA table_info(api_request_log);")
    req_cols = {r[1] for r in cur.fetchall()}
    if "tokens" not in req_cols:
        cur.execute("ALTER TABLE api_request_log ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0;")

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

    # 8. Clinical Dermatology Ingredients (Comedogenicity & Irritancy - Database-backed)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clinical_ingredients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inci TEXT NOT NULL UNIQUE,
        common_name TEXT,
        functional_type TEXT,
        comedogenicity INTEGER NOT NULL DEFAULT 0,
        irritancy INTEGER NOT NULL DEFAULT 0,
        tags TEXT,
        evidence TEXT,
        contraindications TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clinical_ingredients_inci ON clinical_ingredients(inci);")

    # 8b. Verified Ingredient Whitelist (Database-backed for description lookup fallback)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ingredient_whitelist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        canonical_name TEXT,
        category TEXT DEFAULT 'general', -- 'active', 'botanical', 'emollient', 'humectant', 'preservative', 'surfactant', 'general'
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingredient_whitelist_name ON ingredient_whitelist(name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingredient_whitelist_active ON ingredient_whitelist(is_active);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingredient_whitelist_cat ON ingredient_whitelist(category);")

    # 9. Control Table: Pipeline Tasks (Scraping, Ingestion, Refreshes, Batch Jobs)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pipeline_tasks (
        task_id TEXT PRIMARY KEY,
        task_type TEXT NOT NULL,
        target_source TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        fields TEXT,
        params TEXT,
        total_items INTEGER DEFAULT 0,
        processed_items INTEGER DEFAULT 0,
        successful_items INTEGER DEFAULT 0,
        failed_items INTEGER DEFAULT 0,
        skipped_items INTEGER DEFAULT 0,
        progress_percent REAL DEFAULT 0.0,
        current_stage TEXT,
        error_message TEXT,
        metadata TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        duration_seconds REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_status ON pipeline_tasks(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_type ON pipeline_tasks(task_type);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_target ON pipeline_tasks(target_source);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_created ON pipeline_tasks(created_at);")

    # 10. Control Table: Pipeline Subtasks (Task Steps / Page-by-Page Progress)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pipeline_subtasks (
        subtask_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        step_index INTEGER DEFAULT 0,
        total_steps INTEGER DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'pending',
        items_count INTEGER DEFAULT 0,
        items_succeeded INTEGER DEFAULT 0,
        items_failed INTEGER DEFAULT 0,
        error_message TEXT,
        metadata TEXT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        duration_seconds REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_subtasks_task ON pipeline_subtasks(task_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_subtasks_status ON pipeline_subtasks(status);")

    # 11. Control Table: Task Progress & Execution Logs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pipeline_task_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        subtask_id TEXT,
        level TEXT NOT NULL DEFAULT 'INFO',
        message TEXT NOT NULL,
        context_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE,
        FOREIGN KEY (subtask_id) REFERENCES pipeline_subtasks(subtask_id) ON DELETE SET NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_task_logs_task ON pipeline_task_logs(task_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_task_logs_level ON pipeline_task_logs(level);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_task_logs_created ON pipeline_task_logs(created_at);")

    # 12. Control Table: Scraping State & Checkpoints (Incremental / Resumable Crawls)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS scrape_checkpoints (
        store_id TEXT PRIMARY KEY,
        last_task_id TEXT,
        last_page_scraped INTEGER DEFAULT 0,
        total_pages_detected INTEGER DEFAULT 0,
        total_products_scraped INTEGER DEFAULT 0,
        last_scraped_at TIMESTAMP,
        last_successful_sync_at TIMESTAMP,
        sync_cursor TEXT,
        status TEXT DEFAULT 'idle',
        metadata TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
        FOREIGN KEY (last_task_id) REFERENCES pipeline_tasks(task_id) ON DELETE SET NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scrape_checkpoints_status ON scrape_checkpoints(status);")

    # 13. Canonical Products (Cross-Source Product Entity Resolution & Price Comparison)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS canonical_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_title TEXT NOT NULL,
        brand TEXT NOT NULL,
        category TEXT NOT NULL,
        normalized_key TEXT NOT NULL UNIQUE,
        ingredients_raw TEXT,
        ingredients_parsed TEXT,
        actives TEXT,
        ingredient_source TEXT,
        inci_matched_id INTEGER,
        min_price REAL DEFAULT 0.0,
        max_price REAL DEFAULT 0.0,
        store_count INTEGER DEFAULT 1,
        primary_image_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_normalized ON canonical_products(normalized_key);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canonical_brand ON canonical_products(brand);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_canonical ON products(canonical_product_id);")

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
        cur.execute("""
        INSERT OR IGNORE INTO scrape_checkpoints (store_id, status)
        VALUES (?, 'idle');
        """, (s_id,))

    # Seed Default Synonyms
    for alias, canon, cat in DEFAULT_SYNONYMS:
        cur.execute("""
        INSERT INTO ingredient_synonyms (alias, canonical_name, category)
        VALUES (?, ?, ?)
        ON CONFLICT(alias) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            category = excluded.category;
        """, (alias.lower().strip(), canon.strip(), cat))

    # Seed Default Clinical Ingredients (Comedogenicity & Irritancy Database)
    for inci, c_name, f_type, comedo, irrit, tags, evid, contras, notes in DEFAULT_CLINICAL_INGREDIENTS:
        cur.execute("""
        INSERT INTO clinical_ingredients (
            inci, common_name, functional_type, comedogenicity, irritancy,
            tags, evidence, contraindications, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(inci) DO UPDATE SET
            common_name = excluded.common_name,
            functional_type = excluded.functional_type,
            comedogenicity = excluded.comedogenicity,
            irritancy = excluded.irritancy,
            tags = excluded.tags,
            evidence = excluded.evidence,
            contraindications = excluded.contraindications,
            notes = excluded.notes,
            updated_at = CURRENT_TIMESTAMP;
        """, (inci, c_name, f_type, comedo, irrit, json.dumps(tags), evid, json.dumps(contras), notes))

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

def load_clinical_ingredients_from_db(conn=None, db_path=DB_PATH) -> List[Dict[str, Any]]:
    """Loads all clinical ingredients with ratings and metadata from SQLite."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, inci, common_name, functional_type, comedogenicity, irritancy,
               tags, evidence, contraindications, notes
        FROM clinical_ingredients
        ORDER BY inci ASC;
        """)
        rows = cur.fetchall()
        result = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            contras = json.loads(r["contraindications"]) if r["contraindications"] else []
            result.append({
                "id": r["id"],
                "inci": r["inci"],
                "common_name": r["common_name"],
                "functional_type": r["functional_type"],
                "comedogenicity": r["comedogenicity"],
                "irritancy": r["irritancy"],
                "tags": tags,
                "evidence": r["evidence"],
                "contraindications": contras,
                "notes": r["notes"]
            })
        return result
    except Exception as e:
        logger.warning(f"Failed to load clinical ingredients from database: {e}")
        return []
    finally:
        if should_close:
            conn.close()

def add_clinical_ingredient_to_db(inci: str, common_name: str = None, functional_type: str = "active",
                                  comedogenicity: int = 0, irritancy: int = 0,
                                  tags: list = None, evidence: str = None,
                                  contraindications: list = None, notes: str = None,
                                  conn=None, db_path=DB_PATH) -> bool:
    """Inserts or updates a clinical ingredient record in SQLite."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO clinical_ingredients (
            inci, common_name, functional_type, comedogenicity, irritancy,
            tags, evidence, contraindications, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(inci) DO UPDATE SET
            common_name = excluded.common_name,
            functional_type = excluded.functional_type,
            comedogenicity = excluded.comedogenicity,
            irritancy = excluded.irritancy,
            tags = excluded.tags,
            evidence = excluded.evidence,
            contraindications = excluded.contraindications,
            notes = excluded.notes,
            updated_at = CURRENT_TIMESTAMP;
        """, (inci.strip(), common_name.strip() if common_name else None,
              functional_type.strip() if functional_type else "active",
              int(comedogenicity), int(irritancy),
              json.dumps(tags or []), evidence.strip() if evidence else None,
              json.dumps(contraindications or []), notes.strip() if notes else None))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to save clinical ingredient '{inci}': {e}")
        return False
    finally:
        if should_close:
            conn.close()

def load_whitelist_from_db(conn=None, db_path=DB_PATH, active_only: bool = True) -> List[Dict[str, Any]]:
    """
    Loads verified ingredients from SQLite whitelist sorted by length of name DESC.
    Sorting by length descending is essential for greedy longest-match first extraction.
    """
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        query = """
        SELECT id, name, canonical_name, category, is_active, created_at, updated_at
        FROM ingredient_whitelist
        """
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY LENGTH(name) DESC, name ASC;"
        cur.execute(query)
        rows = cur.fetchall()
        if not rows:
            return []
        if cur.description and not isinstance(rows[0], sqlite3.Row):
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, r)) for r in rows]
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Failed to load ingredient whitelist: {e}")
        return []
    finally:
        if should_close:
            conn.close()

def add_whitelist_ingredient(name: str, canonical_name: str = None, category: str = "general",
                             is_active: int = 1, conn=None, db_path=DB_PATH) -> Optional[int]:
    """Inserts or updates a verified ingredient in SQLite whitelist."""
    if not name or not isinstance(name, str) or not name.strip():
        return None
    clean_name = name.strip()
    clean_canon = canonical_name.strip() if canonical_name and isinstance(canonical_name, str) else clean_name
    cat = (category or "general").strip().lower()

    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO ingredient_whitelist (name, canonical_name, category, is_active)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            category = excluded.category,
            is_active = excluded.is_active,
            updated_at = CURRENT_TIMESTAMP;
        """, (clean_name, clean_canon, cat, 1 if is_active else 0))
        conn.commit()
        if cur.lastrowid and cur.lastrowid > 0:
            return cur.lastrowid
        cur.execute("SELECT id FROM ingredient_whitelist WHERE name = ?;", (clean_name,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Failed to add whitelist ingredient '{name}': {e}")
        return None
    finally:
        if should_close:
            conn.close()

def delete_whitelist_ingredient(ingredient_id: int, conn=None, db_path=DB_PATH) -> bool:
    """Deletes an ingredient from the whitelist by ID."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ingredient_whitelist WHERE id = ?;", (ingredient_id,))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to delete whitelist ingredient ID {ingredient_id}: {e}")
        return False
    finally:
        if should_close:
            conn.close()

def update_whitelist_ingredient(ingredient_id: int, updates: Dict[str, Any], conn=None, db_path=DB_PATH) -> bool:
    """Updates an existing whitelist ingredient record."""
    if not updates:
        return False
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cols = []
        vals = []
        allowed = {"name", "canonical_name", "category", "is_active"}
        for k, v in updates.items():
            if k in allowed:
                cols.append(f"{k} = ?")
                if k == "is_active":
                    vals.append(1 if v else 0)
                elif isinstance(v, str):
                    vals.append(v.strip())
                else:
                    vals.append(v)
        if not cols:
            return False
        cols.append("updated_at = CURRENT_TIMESTAMP")
        vals.append(ingredient_id)
        sql = f"UPDATE ingredient_whitelist SET {', '.join(cols)} WHERE id = ?;"
        cur.execute(sql, tuple(vals))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to update whitelist ingredient ID {ingredient_id}: {e}")
        return False
    finally:
        if should_close:
            conn.close()

def get_whitelist_stats(conn=None, db_path=DB_PATH) -> Dict[str, Any]:
    """Returns total count, active count, and category breakdown for ingredient whitelist."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ingredient_whitelist;")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ingredient_whitelist WHERE is_active = 1;")
        active = cur.fetchone()[0]
        cur.execute("""
        SELECT category, COUNT(*) as cnt 
        FROM ingredient_whitelist 
        GROUP BY category 
        ORDER BY cnt DESC;
        """)
        cats = {r["category"]: r["cnt"] for r in cur.fetchall()}
        return {
            "total_ingredients": total,
            "active_ingredients": active,
            "categories": cats
        }
    except Exception as e:
        logger.warning(f"Failed to get whitelist stats: {e}")
        return {"total_ingredients": 0, "active_ingredients": 0, "categories": {}}
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database schema created successfully.")
