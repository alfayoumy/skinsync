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
    from safeguards import CosmeticCompatibilityValidator

try:
    from pipeline.config import cfg
except ImportError:
    from config import cfg

try:
    from pipeline.rate_limiter import gemini_limiter
except ImportError:
    from rate_limiter import gemini_limiter

try:
    from pipeline.db_manager import load_synonyms_from_db
except ImportError:
    from db_manager import load_synonyms_from_db

try:
    from pipeline.logging_config import setup_logging, get_logger
    logger = get_logger("skincare_pipeline.inci_matcher")
except ImportError:
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
    'aloe_vera': ['aloe barbadensis', 'aloe vera'],
    'tranexamic_acid': ['tranexamic acid', 'tranexamic'],
    'kojic_acid': ['kojic acid', 'kojic dipalmitate'],
    'bakuchiol': ['bakuchiol'],
    'urea': ['urea', 'hydroxyethyl urea'],
    'benzoyl_peroxide': ['benzoyl peroxide'],
    'sulfur': ['sulfur', 'colloidal sulfur'],
    'ectoin': ['ectoin'],
    'pha': ['gluconolactone', 'lactobionic acid', 'pha'],
    'ferulic_acid': ['ferulic acid', 'ferulic'],
    'licorice': ['glycyrrhiza glabra', 'licorice', 'dipotassium glycyrrhizate', 'glabridin'],
    'snail_mucin': ['snail secretion filtrate', 'snail mucin'],
    'ferment': ['galactomyces', 'bifida ferment', 'saccharomyces', 'ferment lysate']
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
    'sunscreen': ['homosalate', 'octocrylene', 'ethylhexyl', 'avobenzone', 'tinosorb', 'mexoryl', 'zinc oxide', 'titanium dioxide', 'octisalate', 'uvmune', 'uv', 'spf'],
    'tranexamic': ['tranexamic'],
    'kojic': ['kojic'],
    'bakuchiol': ['bakuchiol'],
    'urea': ['urea'],
    'benzoyl peroxide': ['benzoyl peroxide'],
    'azelaic': ['azelaic'],
    'arbutin': ['arbutin']
}

SYNONYMS: Dict[str, str] = load_synonyms_from_db()

def reload_synonyms() -> Dict[str, str]:
    """Reloads ingredient synonyms from the database into the global SYNONYMS dictionary."""
    global SYNONYMS
    SYNONYMS = load_synonyms_from_db()
    return SYNONYMS

def clean_product_title(title: str) -> str:
    """Strips packaging volume and noise to avoid artificial dissimilarities."""
    if not title or not isinstance(title, str):
        return ""
    # Strip volumes like 50ml, 1.7 oz, 30g, 200 ml, 400ml
    t = re.sub(r'\b\d+(\.\d+)?\s*(?:ml|g|oz|fl\s*oz|kg|ltr)\b', ' ', title, flags=re.IGNORECASE)
    t = re.sub(r'[\[\]\(\)\+*#]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

def normalize_brand(brand: str) -> str:
    """Canonicalizes brand string by removing punctuation, accents, and company suffixes."""
    if not brand or not isinstance(brand, str):
        return ""
    b = brand.lower().strip()
    b = re.sub(r'[\'\"’\-\.,/]', ' ', b)
    suffixes = {'laboratoires', 'laboratoire', 'paris', 'cosmetics', 'dermatologie', 'dermatologique', 'skincare', 'egypt', 'pharma', 'derma'}
    tokens = [t for t in b.split() if t not in suffixes]
    return ' '.join(tokens) if tokens else b

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

def verify_batch_with_gemini(items: List[Dict[str, Any]]) -> Optional[Dict[int, Tuple[bool, float, str]]]:
    """
    Gate 4 Gemini Batch Verifier:
    Evaluates a batch of candidate pairs in a SINGLE Gemini 3.5 Flash-Lite API request.
    This multiplies throughput by N (e.g. 10x) while strictly adhering to:
    - 15 Requests Per Minute (RPM) sliding window pacing
    - 1,500 Requests Per Day (RPD) persistent SQLite tracking

    items format:
    [
        {"id": 101, "store_title": "CeraVe Moisturizing Cream 50ml", "candidate_title": "CeraVe Moisturising Cream"},
        ...
    ]

    Returns:
    {
        101: (is_same: bool, confidence: float, reasoning: str),
        ...
    } or None if rate limited / API error.
    """
    if not items:
        return {}

    _load_env()
    api_key = os.environ.get("GEMINI_API_KEY") or getattr(cfg, "GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("Gemini API key not configured. Skipping Gemini batch evaluation.")
        return None

    # Check and acquire atomic rate limit slot (1 request consumed for the entire batch!)
    if not gemini_limiter.acquire():
        logger.warning("Gemini API call skipped due to daily quota exhaustion (1,500 RPD).")
        return None

    model_name = os.environ.get("GEMINI_MODEL_NAME") or getattr(cfg, "GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
    if model_name.startswith("models/"):
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={api_key}"
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    # Build formatted list of candidate pairs
    pairs_text_list = []
    for idx, item in enumerate(items, 1):
        item_id = item["id"]
        s_title = item.get("store_title") or item.get("title", "")
        c_title = item.get("candidate_title") or item.get("inci_matched_name", "")
        pairs_text_list.append(
            f"{idx}. [ID: {item_id}]\n"
            f"   Product A: \"{s_title}\"\n"
            f"   Product B: \"{c_title}\""
        )
    formatted_pairs = "\n\n".join(pairs_text_list)

    prompt = (
        f"You are a cosmetic chemistry and dermatology expert evaluating skincare product formulations.\n"
        f"For each numbered pair below, determine if Product A and Product B describe the exact same skincare cosmetic formulation.\n\n"
        f"EVALUATION CRITERIA:\n"
        f"- Answer true ONLY if both titles represent the same formulation, brand, and product line (ignoring packaging volume variations like 50ml vs 200ml, minor spelling/accents, or translation variants).\n"
        f"- Answer false if they differ in:\n"
        f"  1. Active ingredients (e.g. Salicylic Acid/BHA vs Glycolic/AHA, or Retinol vs Niacinamide)\n"
        f"  2. Targeted skin conditions (e.g. Bioderma Sensibio for sensitive skin vs Sebium for oily/acne skin)\n"
        f"  3. Product format (e.g. Cleanser vs Gel vs Cream vs Balm)\n"
        f"  4. Formula variant (e.g. AM vs PM, SPF vs non-SPF, Fragrance-Free vs Scented)\n\n"
        f"Pairs to evaluate:\n"
        f"{formatted_pairs}\n\n"
        f"Respond ONLY with a JSON array containing an evaluation object for EVERY ID provided, in this exact format:\n"
        f"[\n"
        f"  {{\"id\": <int>, \"is_exact_same_formula\": <bool>, \"confidence\": <float 0.0-1.0>, \"reasoning\": \"<short sentence>\"}}\n"
        f"]"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json"
        }
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=getattr(cfg, "GEMINI_TIMEOUT_SECONDS", 35.0)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if not candidates:
                logger.warning("Gemini batch response contained no candidates.")
                return None

            content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            res_array = json.loads(content)
            if not isinstance(res_array, list):
                if isinstance(res_array, dict):
                    res_array = res_array.get("results", res_array.get("evaluations", [res_array]))
                else:
                    logger.warning(f"Unexpected Gemini response structure: {type(res_array)}")
                    return None

            results = {}
            for entry in res_array:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get("id")
                if pid is None:
                    continue
                try:
                    pid = int(pid)
                except ValueError:
                    pass
                is_same = bool(entry.get("is_exact_same_formula", False))
                conf = float(entry.get("confidence", 0.0))
                reason = str(entry.get("reasoning", "")).strip()
                results[pid] = (is_same, conf, reason)

            return results
    except Exception as e:
        logger.warning(f"Gemini INCI batch verifier error: {e}")
        return None

def verify_match_with_gemini(store_title: str, candidate_title: str) -> Optional[Tuple[bool, float]]:
    """Single-pair convenience wrapper over verify_batch_with_gemini."""
    res = verify_batch_with_gemini([{"id": 0, "store_title": store_title, "candidate_title": candidate_title}])
    if res and 0 in res:
        is_same, conf, _ = res[0]
        return is_same, conf
    return None

# ==============================================================================
# LOCAL ONNX CROSS-ENCODER RERANKER
# ==============================================================================

_CROSS_ENCODER = None

def get_cross_encoder():
    """Singleton getter for FastEmbed ONNX Cross-Encoder reranker."""
    global _CROSS_ENCODER
    if _CROSS_ENCODER is None and getattr(cfg, "ENABLE_CROSS_ENCODER", True):
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            _CROSS_ENCODER = TextCrossEncoder(model_name=getattr(cfg, "CROSS_ENCODER_MODEL_NAME", "Xenova/ms-marco-MiniLM-L-6-v2"))
            logger.info("FastEmbed ONNX Cross-Encoder loaded successfully.")
        except Exception as e:
            logger.warning(f"Could not load Cross-Encoder: {e}")
            _CROSS_ENCODER = False
    return _CROSS_ENCODER if _CROSS_ENCODER is not False else None

def score_cross_encoder(query: str, doc: str) -> float:
    """Computes joint cross-encoder similarity with sigmoid calibration to [0.0, 1.0]."""
    ce = get_cross_encoder()
    if not ce:
        return 0.5
    try:
        raw_score = list(ce.rerank(query, [doc]))[0]
        # Sigmoid calibration: 1 / (1 + exp(-x))
        return float(1.0 / (1.0 + np.exp(-float(raw_score))))
    except Exception as e:
        logger.debug(f"Cross-encoder scoring error: {e}")
        return 0.5

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

def match_with_inci_db(brand: str, title: str, conn, allow_synchronous_jev: bool = False, scoring_mode: str = "auto", allow_obf: bool = True) -> Optional[Dict[str, Any]]:
    """
    Evaluates candidate formulations across 4 decoupled gates:
    Gate 1: Data Quality Check
    Gate 2: Rule-Based Compatibility Safeguard
    Gate 3: Active Molecule Consistency Check
    Gate 4: Multi-mode Semantic Verification ('local', 'jev', 'gemini', 'all'/'auto')
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

    # Flaw 1 Fix: Fallback to canonical normalized brand matching if direct query yielded 0
    if not candidates:
        norm_b = normalize_brand(brand)
        if norm_b:
            cur.execute("SELECT DISTINCT brand FROM inci_reference WHERE brand IS NOT NULL;")
            all_db_brands = [r[0] for r in cur.fetchall()]
            matched_b = None
            for db_b in all_db_brands:
                if normalize_brand(db_b) == norm_b:
                    matched_b = db_b
                    break
            if not matched_b and HAS_RAPIDFUZZ:
                db_norm_map = {normalize_brand(b): b for b in all_db_brands if b}
                best_res = process.extractOne(norm_b, list(db_norm_map.keys()), scorer=fuzz.token_sort_ratio)
                if best_res and best_res[1] >= 85:
                    matched_b = db_norm_map[best_res[0]]

            if matched_b:
                cur.execute("SELECT id, product_name, ingredients, embedding FROM inci_reference WHERE brand = ?;", (matched_b,))
                candidates = cur.fetchall()

    # Strategy B: Open Beauty Facts API fallback if local candidates absent and OBF allowed
    if not candidates and allow_obf:
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
                "inci_cross_score": 0.85,
                "gate1_quality_status": "passed_obf_verified",
                "gate2_safeguard_status": "passed_direct",
                "gate3_active_status": "passed_active_verified",
                "gate4_cross_status": "passed_obf_verified",
                "gate4_jev_status": "pending_jev",
                "inci_jev_score": None,
                "gate4_gemini_status": None,
                "inci_gemini_score": None,
                "safeguard_log": "OBF live API verified via Gates 1, 2, 3",
                "source": "openbeautyfacts"
            }
        return None

    model = get_embedding_model()
    cleaned_q = clean_product_title(title)

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
            q_emb = list(model.embed([cleaned_q or title]))[0]
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

    # --- 2. HYBRID CANDIDATE SCORING (Calibrated Vector + Dual Token Ratio) ---
    scored_candidates = []
    for idx, cand in enumerate(candidates):
        cand_name = cand['product_name']
        cand_clean = clean_product_title(cand_name)
        v_sim = float(dense_sims[idx]) if dense_sims is not None else 0.0
        
        if HAS_RAPIDFUZZ:
            t_sort = float(fuzz.token_sort_ratio(cleaned_q, cand_clean)) / 100.0
            t_set = float(fuzz.token_set_ratio(cleaned_q, cand_clean)) / 100.0
            t_sim = 0.5 * t_sort + 0.5 * t_set
        else:
            matcher = difflib.SequenceMatcher(None, cleaned_q.lower(), cand_clean.lower())
            t_sim = matcher.ratio()

        if dense_sims is not None:
            # Calibrate BGE vector cosine similarity to full [0.0, 1.0] span
            v_calibrated = max(0.0, min(1.0, (v_sim - 0.50) / 0.50))
            hybrid_score = cfg.DENSE_VECTOR_WEIGHT * v_calibrated + cfg.SPARSE_TOKEN_WEIGHT * t_sim
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

    # Joint Cross-Encoder Evaluation on top compatible candidate
    cross_score = score_cross_encoder(title, cand_name)
    cross_threshold = getattr(cfg, "CROSS_ENCODER_CONFIDENCE_THRESHOLD", 0.70)
    cross_status = "cross_approved" if cross_score >= cross_threshold else "cross_ambiguous"

    # --- GATE 4: JEV + GEMINI FALLBACK EVALUATION ---
    # Case A: Very High Confidence Match -> Auto-approved with 0 LLM calls
    if h_score >= cfg.AUTO_APPROVE_HYBRID_THRESHOLD or (cross_score >= 0.92 and h_score >= 0.85):
        return {
            "ingredients": best_compatible['ingredients'],
            "matched_id": best_compatible['id'],
            "matched_name": cand_name,
            "vector_score": best_compatible['vector_sim'],
            "token_score": best_compatible['token_sim'],
            "hybrid_score": h_score,
            "inci_cross_score": cross_score,
            "gate1_quality_status": g1_status,
            "gate2_safeguard_status": g2_status,
            "gate3_active_status": g3_status,
            "gate4_cross_status": cross_status,
            "gate4_jev_status": "auto_approved_high_confidence",
            "inci_jev_score": None,
            "gate4_gemini_status": None,
            "inci_gemini_score": None,
            "safeguard_log": safeguard_log,
            "source": "skinsafe_matched"
        }

    # Case B: Ambiguity Zone (AMBIGUITY_ZONE_LOW_THRESHOLD - AUTO_APPROVE_HYBRID_THRESHOLD)
    if h_score >= cfg.AMBIGUITY_ZONE_LOW_THRESHOLD:
        jev_status = "pending_jev"
        jev_score = None
        gemini_status = None
        gemini_score = None

        mode = (scoring_mode or "auto").lower().strip()
        if allow_synchronous_jev or mode in ("local", "jev", "gemini", "all"):
            if mode == "local":
                # Local only: evaluate via local Cross-Encoder + deterministic gates, 0 LLM network calls
                jev_status = "local_approved" if cross_score >= cross_threshold else "local_ambiguous"
                jev_score = cross_score
            elif mode == "jev":
                # Jev only: no Gemini fallback
                jev_ok = verify_match_with_jev(title, cand_name)
                if jev_ok is not None:
                    jev_status = "jev_approved" if jev_ok is True else "jev_rejected"
                    jev_score = 0.95 if jev_ok is True else 0.15
                else:
                    jev_status = "jev_rate_limited"
            elif mode == "gemini":
                # Gemini only: no Jev call
                gemini_res = verify_match_with_gemini(title, cand_name)
                if gemini_res is not None:
                    g_ok, g_conf = gemini_res
                    gemini_status = "gemini_approved" if g_ok else "gemini_rejected"
                    gemini_score = g_conf
                else:
                    gemini_status = "pending_gemini"
            else:
                # All / Auto: Primary Jev with Gemini fallback on rate limit / unavailability
                jev_ok = verify_match_with_jev(title, cand_name)
                if jev_ok is not None:
                    jev_status = "jev_approved" if jev_ok is True else "jev_rejected"
                    jev_score = 0.95 if jev_ok is True else 0.15
                else:
                    jev_status = "jev_rate_limited"
                    gemini_res = verify_match_with_gemini(title, cand_name)
                    if gemini_res is not None:
                        g_ok, g_conf = gemini_res
                        gemini_status = "gemini_approved" if g_ok else "gemini_rejected"
                        gemini_score = g_conf

        is_rejected = (jev_status == "jev_rejected" or gemini_status == "gemini_rejected")

        return {
            "ingredients": best_compatible['ingredients'] if not is_rejected else None,
            "matched_id": best_compatible['id'],
            "matched_name": cand_name,
            "vector_score": best_compatible['vector_sim'],
            "token_score": best_compatible['token_sim'],
            "hybrid_score": h_score,
            "inci_cross_score": cross_score,
            "gate1_quality_status": g1_status,
            "gate2_safeguard_status": g2_status,
            "gate3_active_status": g3_status,
            "gate4_cross_status": cross_status,
            "gate4_jev_status": jev_status,
            "inci_jev_score": jev_score,
            "gate4_gemini_status": gemini_status,
            "inci_gemini_score": gemini_score,
            "safeguard_log": safeguard_log,
            "source": "skinsafe_matched"
        }

    return None
