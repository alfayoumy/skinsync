import sqlite3
import os
import json
import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

try:
    from pipeline.logging_config import setup_logging, get_logger
    logger = get_logger("skincare_pipeline.db")
except ImportError:
    logger = logging.getLogger("skincare_pipeline.db")

DB_PATH = os.getenv("SKINSYNC_DB_PATH") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "skincare.db")

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

def init_db(db_path=DB_PATH, seed_defaults: bool = True, conn=None):
    """
    Initializes database schema with proper indexes for fast searches and historical tracking.
    Optionally seeds default tables via dedicated seed scripts.
    """
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
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
        ('tier_resolutions', 'TEXT'),
        ('ingredient_source_url', 'TEXT'),
        ('is_skincare', 'INTEGER DEFAULT 0'),
        ('size_raw', 'TEXT'),
        ('size_value', 'REAL'),
        ('size_unit', 'TEXT'),
        ('normalized_size_ml', 'REAL'),
        ('normalized_size_g', 'REAL'),
        ('unit_price', 'REAL'),
        ('unit_measure', 'TEXT')
    ]
    for col_info in telemetry_cols:
        col_name = col_info[0]
        col_type = col_info[1]
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
        source TEXT DEFAULT 'skinsafe',
        embedding BLOB
    );
    """)
    cur.execute("PRAGMA table_info(inci_reference);")
    existing_ref_cols = {r[1] for r in cur.fetchall()}
    if "embedding" not in existing_ref_cols:
        cur.execute("ALTER TABLE inci_reference ADD COLUMN embedding BLOB;")


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

    # Ensure canonical_products columns
    cur.execute("PRAGMA table_info(canonical_products);")
    existing_can_cols = {r[1] for r in cur.fetchall()}
    can_cols = [
        ('is_skincare', 'INTEGER DEFAULT 1'),
        ('ingredient_source_url', 'TEXT'),
        ('inci_matched_name', 'TEXT'),
        ('gate4_cross_status', 'TEXT'),
        ('gate4_jev_status', 'TEXT'),
        ('gate4_gemini_status', 'TEXT'),
        ('formula_version', 'INTEGER DEFAULT 1'),
        ('formula_hash', 'TEXT'),
        ('formula_hash_unordered', 'TEXT'),
        ('formulation_published_at', 'TIMESTAMP'),
        ('formulation_approval_reason', 'TEXT'),
    ]
    for col_name, col_type in can_cols:
        if col_name not in existing_can_cols:
            cur.execute(f"ALTER TABLE canonical_products ADD COLUMN {col_name} {col_type};")

    # 14. INCI Independent Audit Evidence Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inci_audit_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_product_id INTEGER NOT NULL,
        source_name TEXT NOT NULL,       -- 'skinsafe', 'openbeauty', 'langsearch_inkeedecoder', 'curated_v10', 'admin_override'
        source_record_id TEXT,
        evidence_url TEXT,
        evidence_hash TEXT,
        raw_ingredients_found TEXT,
        parsed_tokens TEXT,
        confidence_score REAL,
        completeness TEXT DEFAULT 'full', -- 'full', 'partial', 'unverified'
        decision TEXT NOT NULL,          -- 'approved_authoritative', 'candidate_evidence', 'rejected_discrepancy'
        discrepancy_notes TEXT,
        validation_flags TEXT,
        audited_by TEXT DEFAULT 'pipeline',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (canonical_product_id) REFERENCES canonical_products(id) ON DELETE CASCADE
    );
    """)
    cur.execute("PRAGMA table_info(inci_audit_evidence);")
    existing_evidence_cols = {r[1] for r in cur.fetchall()}
    evidence_cols = [
        ('source_record_id', 'TEXT'),
        ('evidence_hash', 'TEXT'),
        ('completeness', "TEXT DEFAULT 'full'"),
        ('validation_flags', 'TEXT'),
    ]
    for col_name, col_type in evidence_cols:
        if col_name not in existing_evidence_cols:
            cur.execute(f"ALTER TABLE inci_audit_evidence ADD COLUMN {col_name} {col_type};")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_evidence_canonical ON inci_audit_evidence(canonical_product_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_evidence_source ON inci_audit_evidence(source_name);")

    # Indexes for sub-millisecond query performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_url ON products(product_url);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_price_history_pid ON price_history(product_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inci_ref_brand ON inci_reference(brand);")

    conn.commit()

    # Delegate default table seeding to dedicated seed scripts
    if seed_defaults:
        try:
            from pipeline.seed_defaults import seed_all_defaults
            seed_all_defaults(conn=conn, db_path=db_path)
        except ImportError:
            try:
                from seed_defaults import seed_all_defaults
                seed_all_defaults(conn=conn, db_path=db_path)
            except Exception as se:
                logger.warning(f"Could not automatically seed defaults: {se}")

    if should_close:
        conn.close()
    logger.info("Database initialized successfully at %s", db_path)

def load_stores_from_db(conn=None, db_path=DB_PATH, active_only: bool = True) -> List[Dict[str, Any]]:
    """
    Loads all configured stores from the stores database table.
    """
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        query = "SELECT id, name, domain, platform, is_active, last_sync_at, total_products FROM stores"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY id ASC;"
        cur.execute(query)
        rows = cur.fetchall()
        if not rows:
            return []
        if cur.description and not isinstance(rows[0], sqlite3.Row):
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Failed to load stores from database: {e}")
        return []
    finally:
        if should_close:
            conn.close()

def add_store_to_db(store_id: str, name: str, domain: str, platform: str = 'shopify',
                     is_active: int = 1, conn=None, db_path=DB_PATH) -> bool:
    """Inserts or updates a store record in the stores database table."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO stores (id, name, domain, platform, is_active)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            domain = excluded.domain,
            platform = excluded.platform,
            is_active = excluded.is_active;
        """, (store_id.strip(), name.strip(), domain.strip(), platform.strip(), 1 if is_active else 0))
        cur.execute("""
        INSERT OR IGNORE INTO scrape_checkpoints (store_id, status)
        VALUES (?, 'idle');
        """, (store_id.strip(),))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to add store '{store_id}': {e}")
        return False
    finally:
        if should_close:
            conn.close()

def load_synonyms_from_db(conn=None, db_path=DB_PATH) -> dict:
    """
    Loads all ingredient synonyms from the ingredient_synonyms database table into a dictionary.
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
        return {r[0].lower().strip(): r[1].strip() for r in rows}
    except Exception as e:
        logger.warning(f"Failed to load synonyms from database: {e}")
        return {}
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

    # Strictly reject any ingredients containing Arabic script characters
    arabic_pattern = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u0870-\u08FF\uFB50-\uFDFF\uFE70-\uFEFC]')
    if arabic_pattern.search(clean_name) or (clean_canon and arabic_pattern.search(clean_canon)):
        logger.warning(f"Rejected whitelist ingredient containing Arabic text: '{clean_name}'")
        return None

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

def add_inci_audit_evidence(
    canonical_product_id: int,
    source_name: str,
    raw_ingredients: str,
    parsed_tokens: Optional[List[str]] = None,
    evidence_url: Optional[str] = None,
    confidence_score: Optional[float] = None,
    decision: str = "candidate_evidence",
    discrepancy_notes: Optional[str] = None,
    audited_by: str = "pipeline",
    conn=None,
    db_path=DB_PATH
) -> Optional[int]:
    """Records independent verification evidence or discrepancy audit for a canonical product."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO inci_audit_evidence (
            canonical_product_id, source_name, evidence_url, raw_ingredients_found,
            parsed_tokens, confidence_score, decision, discrepancy_notes, audited_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            canonical_product_id,
            source_name.strip(),
            evidence_url.strip() if evidence_url else None,
            raw_ingredients.strip() if raw_ingredients else None,
            json.dumps(parsed_tokens) if parsed_tokens else None,
            confidence_score,
            decision,
            discrepancy_notes,
            audited_by
        ))
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        logger.error(f"Failed to record INCI audit evidence for canonical ID {canonical_product_id}: {e}")
        return None
    finally:
        if should_close:
            conn.close()

def get_inci_audit_evidence(canonical_product_id: int, conn=None, db_path=DB_PATH) -> List[Dict[str, Any]]:
    """Fetches all independent verification evidence and audit decisions for a canonical product."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, canonical_product_id, source_name, evidence_url, raw_ingredients_found,
               parsed_tokens, confidence_score, decision, discrepancy_notes, audited_by, created_at
        FROM inci_audit_evidence
        WHERE canonical_product_id = ?
        ORDER BY created_at DESC;
        """, (canonical_product_id,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Failed to fetch INCI audit evidence for canonical ID {canonical_product_id}: {e}")
        return []
    finally:
        if should_close:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database schema created successfully.")
