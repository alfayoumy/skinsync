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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (store_id) REFERENCES stores(id)
    );
    """)

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

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully at %s", db_path)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database schema created successfully.")
