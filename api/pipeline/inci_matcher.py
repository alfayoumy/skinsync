"""
INCI Formulation Matcher & Normalizer.
Features 4-Gate Decoupled Hybrid Vector Architecture:
- Gate 1: Raw Data Quality Validation
- Gate 2: Deterministic Rule-Based Compatibility Safeguard
- Gate 3: Active Molecule Consistency Verification
- Gate 4: Decoupled Jev 1.13 Free Semantic Verification (Paced & Rate-Limit Protected)
- Strategy A (Store Self-Enrichment) & Strategy B (Open Beauty Facts Live API Fallback)
"""

import re
import html
import time
import logging
import urllib.request
import urllib.error
import urllib.parse
import json
import os
from typing import Optional, Tuple, Set, List, Dict, Any

try:
    from rapidfuzz import process, fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False

import numpy as np

try:
    from pipeline.safeguards import CosmeticCompatibilityValidator
except ImportError:
    try:
        from repo.api.pipeline.safeguards import CosmeticCompatibilityValidator
    except ImportError:
        from safeguards import CosmeticCompatibilityValidator

try:
    from pipeline.config import cfg
except ImportError:
    try:
        from repo.api.pipeline.config import cfg
    except ImportError:
        from config import cfg

logger = logging.getLogger("skincare_pipeline.inci_matcher")

KEY_ACTIVES = {
    'hyaluronic_acid': ['hyaluronic acid', 'sodium hyaluronate', 'hydrolyzed hyaluronic', 'hyaluronate', 'hayluronic'],
    'vitamin_b5': ['panthenol', 'd-panthenol', 'pro-vitamin b5'],
    'niacinamide': ['niacinamide', 'nicotinamide', 'vitamin b3'],
    'vitamin_c': ['ascorbic acid', 'l-ascorbic', 'ethyl ascorbic', 'magnesium ascorbyl', 'sodium ascorbyl', 'ascorbyl glucoside', 'vitamin c'],
    'retinoid': ['retinol', 'retinyl palmitate', 'retinal', 'hydroxypinacolone retinoate', 'adapalene'],
    'salicylic_acid': ['salicylic acid', 'bha', 'betaine salicylate', 'willow bark'],
    'aha': ['glycolic acid', 'lactic acid', 'mandelic acid', 'malic acid', 'citric acid'],
    'ceramides': ['ceramide np', 'ceramide ap', 'ceramide eop', 'ceramide 3', 'ceramide', 'phytosphingosine'],
    'centella': ['centella asiatica', 'madecassoside', 'asiaticoside', 'asiatic acid', 'madecassic acid', 'cica'],
    'peptides': ['peptide', 'matrixyl', 'copper tripeptide', 'palmitoyl', 'argireline', 'oligopeptide'],
    'azelaic_acid': ['azelaic acid', 'potassium azeloyl diglycinate'],
    'arbutin': ['alpha-arbutin', 'arbutin', 'alpha arbutin'],
    'zinc': ['zinc pca', 'zinc oxide', 'zinc gluconate'],
    'squalane': ['squalane', 'squalene'],
    'tea_tree': ['tea tree', 'melaleuca alternifolia'],
    'collagen': ['hydrolyzed collagen', 'marine collagen', 'collagen'],
    'aloe_vera': ['aloe barbadensis', 'aloe vera']
}

ACTIVE_MARKERS = {
    'retinol': ['retinol', 'retinyl', 'retinal', 'adapalene'],
    'niacinamide': ['niacinamide', 'nicotinamide', 'vitamin b3'],
    'hyaluronic': ['hyaluron', 'sodium hyaluronate', 'hayluronic'],
    'vitamin c': ['ascorb', 'vitamin c'],
    'salicylic': ['salicylic', 'bha'],
    'glycolic': ['glycolic', 'aha'],
    'centella': ['centella', 'madecassoside', 'asiaticoside', 'cica'],
    'ceramide': ['ceramide'],
    'sunscreen': ['homosalate', 'octocrylene', 'ethylhexyl', 'avobenzone', 'tinosorb', 'mexoryl', 'zinc oxide', 'titanium dioxide', 'octisalate', 'uvmune', 'uv', 'spf']
}

SYNONYMS = {
    'aqua': 'water', 'eau': 'water', 'purified water': 'water', 'deionized water': 'water',
    'parfum': 'fragrance', 'perfume': 'fragrance', 'fragrance (parfum)': 'fragrance',
    'alcohol denat.': 'alcohol denat', 'alcohol denat': 'alcohol denat',
    'sodium lauryl sulfate': 'sls', 'sodium laureth sulfate': 'sles',
    'cocos nucifera (coconut) oil': 'coconut oil', 'coconut oil': 'coconut oil',
    'theobroma cacao (cocoa) seed butter': 'cocoa butter', 'cocoa butter': 'cocoa butter',
    'butyrospermum parkii (shea) butter': 'shea butter', 'shea butter': 'shea butter',
    'simmondsia chinensis (jojoba) seed oil': 'jojoba oil', 'jojoba oil': 'jojoba oil'
}

def extract_ingredients_from_html(body_html: str) -> Optional[str]:
    """Extracts ingredient block from Shopify product body_html using common headings."""
    if not body_html or not isinstance(body_html, str):
        return None

    text = re.sub(r'<br\s*/?>', '\n', body_html, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|li|h\d)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)

    patterns = [
        r'(?:ingredients|key ingredients|active ingredients|composition|formula|المكونات)\s*[:：\n]([\s\S]+?)(?=\n\s*(?:how to use|directions|benefits|warning|precautions|features|size|volume|suitable for|$))',
        r'(?:ingredients|key ingredients)\s*[:：\n]([\s\S]+)',
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) >= cfg.MIN_INGREDIENT_RAW_LEN and (',' in candidate or len(candidate.split()) >= cfg.MIN_INGREDIENT_COUNT):
                return candidate[:cfg.MAX_INGREDIENT_RAW_LEN]

    return None

def parse_and_normalize_ingredients(text: str) -> Tuple[List[str], List[str]]:
    """Cleans raw ingredient string into normalized list and detects key actives."""
    if not text or not isinstance(text, str):
        return [], []

    raw_tokens = re.split(r'[,;•\n\r/]+', text)
    cleaned = []
    actives = set()

    for token in raw_tokens:
        token = token.strip().lower()
        if not token or len(token) < 2:
            continue
        token = re.sub(r'^\d+(\.\d+)?%\s*(pure\s*)?', '', token)
        token = token.rstrip('.').strip()

        for act_cat, keywords in KEY_ACTIVES.items():
            if any(k in token for k in keywords):
                actives.add(act_cat)

        canon = SYNONYMS.get(token, token)
        cleaned.append(canon)

    return cleaned, sorted(list(actives))

def _load_env():
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.getcwd(), "repo", "api", ".env"),
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

_load_env()
_LAST_JEV_CALL_TS = 0.0

def verify_match_with_jev(store_title: str, candidate_title: str) -> Optional[bool]:
    """
    Gate 4 Verifier: Calls Jev 1.13 Free (via OpenCode Zen or TypeSafe).
    Includes rate-limiting (configurable interval) and 429 exponential backoff protection.
    """
    global _LAST_JEV_CALL_TS
    _load_env()
    opencode_key = os.environ.get("OPENCODE_API_KEY")
    typesafe_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    
    if opencode_key:
        api_key = opencode_key
        url = "https://opencode.ai/zen/v1/systemone"
        model_name = cfg.JEV_MODEL_NAME
    elif typesafe_key:
        api_key = typesafe_key
        url = "https://api.typesafe.ai/v1/systemone"
        model_name = "jev-latest"
    else:
        return None

    # Paced rate-limit interval
    now = time.time()
    elapsed = now - _LAST_JEV_CALL_TS
    if elapsed < cfg.JEV_REQUEST_INTERVAL_SECONDS:
        time.sleep(cfg.JEV_REQUEST_INTERVAL_SECONDS - elapsed)
    _LAST_JEV_CALL_TS = time.time()

    payload = {
        "model": model_name,
        "state": f"Store Product Title: \"{store_title}\".\nReference Database Title: \"{candidate_title}\".",
        "questions": {
            "is_exact_same_formula": {
                "type": "noul",
                "instructions": "Do both titles describe the exact same skincare cosmetic formulation? (Answer false if they have different active ingredients such as Salicylic Acid/SA, different SPF sun protection, different day/night versions, or different targeted skin lines like Sensibio vs Sebium)."
            }
        }
    }
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; SkincareAI/2.0)"
            }
        )
        with urllib.request.urlopen(req, timeout=cfg.JEV_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            noul = data.get("answers", {}).get("is_exact_same_formula", {}).get("noul")
            if noul is not None:
                return float(noul) >= cfg.JEV_CONFIDENCE_THRESHOLD
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning(f"OpenCode Zen rate limit reached (HTTP 429). Backing off for {cfg.JEV_RATE_LIMIT_BACKOFF_SECONDS}s to preserve quota...")
            time.sleep(cfg.JEV_RATE_LIMIT_BACKOFF_SECONDS)
        else:
            logger.warning(f"Jev INCI verifier HTTP error {e.code}: {e.reason}")
        return None
    except Exception as e:
        logger.warning(f"Jev INCI verifier skipped: {e}")
        return None

# ==============================================================================
# PER-GATE VALIDATORS
# ==============================================================================

def verify_gate1_data_quality(ingredients: str, source: str) -> Tuple[bool, str]:
    """Gate 1: Raw Data Quality Validation."""
    if not ingredients or len(ingredients.strip()) < cfg.MIN_INGREDIENT_RAW_LEN:
        return False, "failed_empty_or_too_short"
    tokens = [t.strip() for t in re.split(r'[,;•\n\r/]+', ingredients) if len(t.strip()) > 1]
    if len(tokens) < cfg.MIN_INGREDIENT_COUNT:
        return False, "failed_too_few_entities"
    return True, f"passed_quality_{source}"

def verify_gate3_active_consistency(title: str, ingredients: str) -> Tuple[bool, str]:
    """Gate 3: Active Molecule Consistency Verification."""
    title_l = title.lower()
    raw_l = ingredients.lower()
    for claim, markers in ACTIVE_MARKERS.items():
        if re.search(r'\b' + re.escape(claim) + r'\b', title_l):
            if not any(m in raw_l for m in markers):
                return False, f"rejected_missing_claimed_active_{claim}"
    return True, "passed_active_verified"

# ==============================================================================
# STRATEGY B: OPEN BEAUTY FACTS API FALLBACK
# ==============================================================================

def query_open_beauty_facts(brand: str, title: str) -> Optional[Dict[str, Any]]:
    """Strategy B: Queries Open Beauty Facts API with quality filtering."""
    clean_t = re.sub(r'\b\d+\s*(?:ml|g|oz|fl\s*oz)\b|[()\[\]\+]', ' ', title, flags=re.I).strip()
    words = clean_t.split()[:4]
    search_str = f"{brand} {' '.join(words)}"
    encoded = urllib.parse.quote(search_str)
    url = f"https://world.openbeautyfacts.org/cgi/search.pl?search_terms={encoded}&search_simple=1&action=process&json=1&page_size={cfg.OPENBEAUTYFACTS_PAGE_SIZE}"
    
    req = urllib.request.Request(url, headers={"User-Agent": "SkincareSync/1.0 (contact@skinsync.app)"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.OPENBEAUTYFACTS_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for p in data.get("products", []):
                p_name = p.get("product_name") or p.get("product_name_en") or ""
                ings = p.get("ingredients_text") or p.get("ingredients_text_en") or ""
                if p_name and ings and len(ings) >= cfg.MIN_INGREDIENT_RAW_LEN:
                    g1_ok, _ = verify_gate1_data_quality(ings, "openbeautyfacts")
                    if not g1_ok:
                        continue
                    g2_ok, _ = CosmeticCompatibilityValidator.validate_pair(title, p_name)
                    if not g2_ok:
                        continue
                    g3_ok, _ = verify_gate3_active_consistency(title, ings)
                    if not g3_ok:
                        continue

                    return {
                        "product_name": p_name,
                        "ingredients": ings,
                        "brand": p.get("brands") or brand,
                        "source": "openbeautyfacts"
                    }
    except Exception as e:
        logger.debug(f"Open Beauty Facts API query skipped: {e}")
    return None

# ==============================================================================
# DENSE VECTOR MODEL SINGLETON
# ==============================================================================

_EMBEDDING_MODEL = None

def get_embedding_model():
    """Singleton getter for FastEmbed ONNX embedding model."""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        try:
            from fastembed import TextEmbedding
            _EMBEDDING_MODEL = TextEmbedding(model_name=cfg.FASTEMBED_MODEL_NAME)
            logger.info(f"FastEmbed ONNX model ({cfg.FASTEMBED_MODEL_NAME}) loaded successfully.")
        except Exception as e:
            logger.warning(f"Could not load FastEmbed model: {e}")
            _EMBEDDING_MODEL = False
    return _EMBEDDING_MODEL if _EMBEDDING_MODEL is not False else None

# ==============================================================================
# 4-GATE DECOUPLED INCI MATCHER
# ==============================================================================

def match_with_inci_db(brand: str, title: str, conn, allow_synchronous_jev: bool = False) -> Optional[Dict[str, Any]]:
    """
    Evaluates candidate formulations across 4 decoupled gates:
    Gate 1: Data Quality Check
    Gate 2: Rule-Based Compatibility Safeguard
    Gate 3: Active Molecule Consistency Check
    Gate 4: Decoupled Jev State Handling (Auto-approved if >= 0.93, pending_jev if 0.70-0.92)
    """
    if not brand or not title:
        return None

    cur = conn.cursor()
    # Search within the same brand first (bidirectional)
    cur.execute("""
    SELECT id, product_name, ingredients, embedding FROM inci_reference
    WHERE LOWER(brand) = LOWER(?) 
       OR LOWER(?) LIKE '%' || LOWER(brand) || '%'
       OR LOWER(brand) LIKE '%' || LOWER(?) || '%';
    """, (brand, brand, brand))

    candidates = cur.fetchall()

    # Strategy B: Open Beauty Facts API fallback if local candidates absent
    if not candidates:
        obf_result = query_open_beauty_facts(brand, title)
        if obf_result:
            logger.info(f"Open Beauty Facts MATCHED: '{title}' == '{obf_result['product_name']}'")
            cur.execute("""
            INSERT INTO inci_reference (brand, product_name, ingredients, source)
            VALUES (?, ?, ?, 'openbeautyfacts');
            """, (brand, obf_result["product_name"], obf_result["ingredients"]))
            conn.commit()
            new_id = cur.lastrowid
            return {
                "ingredients": obf_result["ingredients"],
                "matched_id": new_id,
                "matched_name": obf_result["product_name"],
                "vector_score": 0.85,
                "token_score": 0.85,
                "hybrid_score": 0.85,
                "gate1_quality_status": "passed_obf_verified",
                "gate2_safeguard_status": "passed_direct",
                "gate3_active_status": "passed_active_verified",
                "gate4_jev_status": "pending_jev",
                "inci_jev_score": None,
                "safeguard_log": "OBF live API verified via Gates 1, 2, 3",
                "source": "openbeautyfacts"
            }
        return None

    model = get_embedding_model()

    # --- 1. DENSE VECTOR RETRIEVAL (FastEmbed + NumPy SQLite BLOB) ---
    if model is not None:
        missing = [row for row in candidates if row['embedding'] is None]
        if missing:
            try:
                embs = list(model.embed([r['product_name'] for r in missing]))
                for r, emb in zip(missing, embs):
                    cur.execute("UPDATE inci_reference SET embedding = ? WHERE id = ?;", (emb.astype(np.float32).tobytes(), r['id']))
                conn.commit()
                cur.execute("""
                SELECT id, product_name, ingredients, embedding FROM inci_reference
                WHERE LOWER(brand) = LOWER(?) 
                   OR LOWER(?) LIKE '%' || LOWER(brand) || '%'
                   OR LOWER(brand) LIKE '%' || LOWER(?) || '%';
                """, (brand, brand, brand))
                candidates = cur.fetchall()
            except Exception as e:
                logger.warning(f"FastEmbed batch embedding failed: {e}")

        try:
            q_emb = list(model.embed([title]))[0]
            q_vec = q_emb / np.linalg.norm(q_emb)

            db_vecs = np.array([np.frombuffer(r['embedding'], dtype=np.float32) for r in candidates])
            db_norms = np.linalg.norm(db_vecs, axis=1, keepdims=True)
            db_vecs_norm = db_vecs / db_norms

            dense_sims = np.dot(db_vecs_norm, q_vec)
        except Exception as e:
            logger.warning(f"Vector search computation failed: {e}")
            dense_sims = None
    else:
        dense_sims = None

    # --- 2. HYBRID CANDIDATE SCORING ---
    scored_candidates = []
    for idx, cand in enumerate(candidates):
        cand_name = cand['product_name']
        v_sim = float(dense_sims[idx]) if dense_sims is not None else 0.0
        
        if HAS_RAPIDFUZZ:
            t_sim = float(fuzz.token_sort_ratio(title, cand_name)) / 100.0
        else:
            matcher = difflib.SequenceMatcher(None, title.lower(), cand_name.lower())
            t_sim = matcher.ratio()

        if dense_sims is not None:
            hybrid_score = cfg.DENSE_VECTOR_WEIGHT * v_sim + cfg.SPARSE_TOKEN_WEIGHT * t_sim
        else:
            hybrid_score = t_sim

        scored_candidates.append({
            'idx': idx,
            'id': cand['id'],
            'name': cand_name,
            'hybrid_score': hybrid_score,
            'vector_sim': v_sim,
            'token_sim': t_sim,
            'ingredients': cand['ingredients']
        })

    scored_candidates.sort(key=lambda x: x['hybrid_score'], reverse=True)

    # --- GATES 1, 2, 3: SELECT BEST COMPATIBLE CANDIDATE ---
    best_compatible = None
    bypassed_notes = []
    g1_status = "passed_quality_skinsafe"
    g2_status = "passed_direct"
    g3_status = "passed_active_verified"

    for cand in scored_candidates[:cfg.TOP_K_CANDIDATES]:
        cand_name = cand['name']
        cand_ings = cand['ingredients']

        # Gate 1: Quality Check
        g1_ok, g1_res = verify_gate1_data_quality(cand_ings, "skinsafe")
        if not g1_ok:
            bypassed_notes.append(f"Bypassed '{cand_name}' (Gate 1: {g1_res})")
            continue

        # Gate 2: Rule Safeguard (Cosmetic Incompatibilities)
        g2_ok, rej_reason = CosmeticCompatibilityValidator.validate_pair(title, cand_name)
        if not g2_ok:
            bypassed_notes.append(f"Bypassed '{cand_name}' (Gate 2: {rej_reason})")
            continue

        # Gate 3: Active Consistency Check
        g3_ok, g3_res = verify_gate3_active_consistency(title, cand_ings)
        if not g3_ok:
            bypassed_notes.append(f"Bypassed '{cand_name}' (Gate 3: {g3_res})")
            continue

        best_compatible = cand
        if bypassed_notes:
            g2_status = "passed_bypassed_clashes"
        break

    if not best_compatible:
        return None

    h_score = best_compatible['hybrid_score']
    cand_name = best_compatible['name']
    safeguard_log = "; ".join(bypassed_notes) if bypassed_notes else "Clean direct match"

    # --- GATE 4: JEV DECOUPLED STATE EVALUATION ---
    # Case A: Very High Confidence Match (>= AUTO_APPROVE_HYBRID_THRESHOLD) -> Auto-approved with 0 LLM calls
    if h_score >= cfg.AUTO_APPROVE_HYBRID_THRESHOLD:
        return {
            "ingredients": best_compatible['ingredients'],
            "matched_id": best_compatible['id'],
            "matched_name": cand_name,
            "vector_score": best_compatible['vector_sim'],
            "token_score": best_compatible['token_sim'],
            "hybrid_score": h_score,
            "gate1_quality_status": g1_status,
            "gate2_safeguard_status": g2_status,
            "gate3_active_status": g3_status,
            "gate4_jev_status": "auto_approved_high_confidence",
            "inci_jev_score": None,
            "safeguard_log": safeguard_log,
            "source": "skinsafe_matched"
        }

    # Case B: Ambiguity Zone (AMBIGUITY_ZONE_LOW_THRESHOLD - AUTO_APPROVE_HYBRID_THRESHOLD) -> Marked for Decoupled Jev Processing!
    if h_score >= cfg.AMBIGUITY_ZONE_LOW_THRESHOLD:
        if allow_synchronous_jev:
            jev_ok = verify_match_with_jev(title, cand_name)
            jev_status = "jev_approved" if jev_ok is True else ("jev_rejected" if jev_ok is False else "jev_skipped")
            jev_score = 0.95 if jev_ok is True else (0.15 if jev_ok is False else None)
        else:
            # DECOUPLED: Mark as pending_jev without stalling ingestion!
            jev_status = "pending_jev"
            jev_score = None

        return {
            "ingredients": best_compatible['ingredients'] if jev_status != "jev_rejected" else None,
            "matched_id": best_compatible['id'],
            "matched_name": cand_name,
            "vector_score": best_compatible['vector_sim'],
            "token_score": best_compatible['token_sim'],
            "hybrid_score": h_score,
            "gate1_quality_status": g1_status,
            "gate2_safeguard_status": g2_status,
            "gate3_active_status": g3_status,
            "gate4_jev_status": jev_status,
            "inci_jev_score": jev_score,
            "safeguard_log": safeguard_log,
            "source": "skinsafe_matched"
        }

    return None
