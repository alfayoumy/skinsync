"""
Product Title Enrichment & Canonical Title Reconciliation Engine.
Repairs truncated product titles by reconciling curated catalog titles,
extracting descriptive qualifiers from URL handles, prefixing bare generic nouns with brand names,
and sanitizing dangling punctuation.
"""

import os
import re
import json
import logging
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd

from pipeline.db_manager import get_connection
from pipeline.logging_config import get_logger
from pipeline.task_tracker import (
    create_task, complete_task, fail_task, update_task_progress, log_task_event,
    create_subtask, complete_subtask
)

logger = get_logger("skincare_pipeline.title_enricher")

GENERIC_TERMS = {
    'lip oil', 'toner', 'tint', 'mousse', 'dry oil', 'sunscreen', 'night cream', 'day cream',
    'eye gel', 'face scrub', 'cleanser', 'face wash', 'body mist', 'hair mask', 'lip balm',
    'serum', 'face serum', 'shower gel', 'body lotion', 'shampoo', 'conditioner', 'correct',
    'natural glow', 'majesty', 'bliss', 'lush', 'sleep', 'fleur', 'hope', 'royale',
    'natmag', 'nathair', 'blusher', 'face mask', 'body splash', 'hand cream', 'foot cream',
    'pouch', 'grow brush', 'lip scrub', 'body scrub', 'eye cream', 'facial cleanser',
    'facial wash', 'micellar water', 'makeup remover', 'sun spray', 'sun lotion', 'sun gel'
}

HANDLE_NOISE_TOKENS = {
    'kr', 'dr', 'us', 'w', 'egp', 'bottle', 'box', 'size', 'ml', 'gm', 'oz', 'fl',
    'copy', 'new', 'free', 'promo', 'offer', 'pack', 'piece', 'set', 'item', 'off',
    'eid', 'ramadan', 'mother', 'mothers', 'day', 'sale', 'discount', 'deal', 'gift',
    'bundle', 'sachet', 'pouch', 'pl', 'm', 'f', 'special', 'scent', 'scents', 'unisex',
    'tube', 'kit', 'promopack'
}

def is_noise_token(tok: str, brand: Optional[str] = None, prev_tok: Optional[str] = None) -> bool:
    """Checks if a handle token is noise, promotion, pack size, volume, or brand repetition."""
    t = tok.lower()
    # Preserve cosmetic claims like aluminum-free, fragrance-free, oil-free, etc.
    if t == 'free' and prev_tok and prev_tok.lower() in {
        'aluminum', 'fragrance', 'alcohol', 'paraben', 'sulfate', 'oil', 'cruelty', 'gluten', 'silicone', 'soap'
    }:
        return False
    if t in HANDLE_NOISE_TOKENS:
        return True
    if re.match(r'^\d+(\.\d+)?(ml|g|gm|kg|oz|fl|l|m|cm|mm|pcs?|x|pack)?$', t):
        return True
    if re.match(r'^\d+x\d+$', t):
        return True
    if t.isdigit():
        return True
    if brand:
        b_norm = re.sub(r'[^a-z0-9]', '', brand.lower())
        t_norm = re.sub(r'[^a-z0-9]', '', t)
        if t_norm and len(t_norm) >= 3 and t_norm == b_norm:
            return True
    return False

def load_v10_title_lookup(csv_path: Optional[str] = None) -> Dict[str, str]:
    """
    Loads curated full_product_title mappings from master_product_database_v10_final.csv.
    """
    if not csv_path:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "master_product_database_v10_final.csv")

    if not os.path.exists(csv_path):
        return {}

    try:
        df = pd.read_csv(csv_path)
        lookup = {}
        for _, row in df.iterrows():
            url = str(row.get('url', '')).strip()
            full = str(row.get('full_product_title', '')).strip()
            if url and full and full.lower() != 'nan':
                lookup[url] = full
        return lookup
    except Exception as e:
        logger.warning(f"Could not load v10 title lookup from {csv_path}: {e}")
        return {}

def is_brand_in_title(brand: str, title: str) -> bool:
    """
    Checks if brand is already present at or near the start of the title,
    normalizing whitespace, casing, and punctuation (e.g. Bio-Oil == Bio Oil).
    """
    if not brand or not title:
        return False
    b_norm = re.sub(r'[^a-z0-9]', '', brand.lower())
    t_norm = re.sub(r'[^a-z0-9]', '', title.lower())
    if not b_norm:
        return False
    # Check exact prefix or substring near beginning
    if t_norm.startswith(b_norm):
        return True
    idx = t_norm.find(b_norm)
    return idx != -1 and idx <= 5

def clean_trailing_punctuation(title: str) -> str:
    """Strips dangling hyphens, trailing slashes, commas, and ellipses (preserves SPF+)."""
    if not title:
        return ""
    t = re.sub(r'\s*\.\.\.$', '', title.strip())
    t = re.sub(r'\s*…$', '', t.strip())
    t = re.sub(r'[\-–—/,\s]+$', '', t.strip())
    return t.strip()

def reconcile_title_from_handle(title: str, handle: str, brand: str) -> str:
    """
    Detects if the title was clipped or shortened relative to the URL handle,
    and appends the missing descriptive qualifiers from the handle.
    Filters out promotional campaigns, pack sizes, volume units, and duplicate tokens.
    """
    if not handle or not isinstance(handle, str) or not title or not isinstance(title, str):
        return title

    # Clean handle slug
    clean_h = handle.strip().replace('_', '-')
    clean_h = re.sub(r'-\d{6,}$', '', clean_h)
    clean_h = re.sub(r'-copy(-\d+)?$', '', clean_h)
    clean_h = re.sub(r'-\d+$', '', clean_h)

    h_tokens = [tok for tok in clean_h.split('-') if tok]
    if len(h_tokens) < 3:
        return title

    t_tokens = [re.sub(r'[^a-z0-9]', '', w.lower()) for w in title.split() if re.sub(r'[^a-z0-9]', '', w.lower())]
    if not t_tokens:
        return title

    last_t = t_tokens[-1]
    match_idx = -1

    # 1. Exact token match precedence (search backwards)
    for i in range(len(h_tokens) - 1, -1, -1):
        if h_tokens[i].lower() == last_t:
            match_idx = i
            break

    # 2. Prefix token match fallback (only for words with >= 4 characters to avoid false matches)
    if match_idx == -1 and len(last_t) >= 4:
        for i in range(len(h_tokens) - 1, -1, -1):
            if h_tokens[i].lower().startswith(last_t):
                match_idx = i
                break

    if match_idx != -1 and match_idx < len(h_tokens) - 1:
        trailing_tokens = h_tokens[match_idx + 1:]
        valid_trailing = []
        for i, tok in enumerate(trailing_tokens):
            prev_tok = trailing_tokens[i - 1] if i > 0 else None
            if is_noise_token(tok, brand, prev_tok):
                continue
            # Keep single letters only if part of vitamin (e.g. vitamin c)
            if len(tok) == 1:
                if i > 0 and trailing_tokens[i - 1].lower() == 'vitamin':
                    valid_trailing.append(tok)
                continue
            valid_trailing.append(tok)

        # Only append if trailing tokens contain substantive qualifiers and not already in title
        if valid_trailing and len(valid_trailing) <= 6:
            already_in_title = 0
            for tr in valid_trailing:
                tr_l = tr.lower()
                if any(tr_l == tok or (len(tr_l) >= 4 and tok.startswith(tr_l)) for tok in t_tokens):
                    already_in_title += 1
            if already_in_title > 0:
                return title

            trail_str = ' '.join(valid_trailing).title()
            trail_str = trail_str.replace('Age Defying', 'Age-Defying')
            trail_str = trail_str.replace('Spf 50', 'SPF50+').replace('Spf 30', 'SPF30+')
            trail_str = trail_str.replace('Spf50', 'SPF50+').replace('Spf30', 'SPF30+')
            trail_str = re.sub(r'\bVitamin C\b', 'Vitamin C', trail_str, flags=re.IGNORECASE)
            trail_str = re.sub(r'\bAluminum Free\b', 'Aluminum-Free', trail_str, flags=re.IGNORECASE)
            trail_str = re.sub(r'\bFragrance Free\b', 'Fragrance-Free', trail_str, flags=re.IGNORECASE)
            trail_str = re.sub(r'\bOil Free\b', 'Oil-Free', trail_str, flags=re.IGNORECASE)
            trail_str = re.sub(r'\bParaben Free\b', 'Paraben-Free', trail_str, flags=re.IGNORECASE)

            clean_t = clean_trailing_punctuation(title)
            # Avoid appending if already partially in title
            t_norm = re.sub(r'[^a-z0-9]', '', clean_t.lower())
            trail_norm = re.sub(r'[^a-z0-9]', '', trail_str.lower())
            if trail_norm in t_norm:
                return title

            if not any(clean_t.endswith(sep) for sep in ['-', '–', '—', ':']):
                return f"{clean_t} - {trail_str}"
            else:
                return f"{clean_t} {trail_str}"

    return title

def enrich_product_title(
    title: str,
    handle: Optional[str] = None,
    brand: Optional[str] = None,
    url: Optional[str] = None,
    v10_lookup: Optional[Dict[str, str]] = None
) -> Tuple[str, bool, str]:
    """
    Applies the full title enrichment pipeline to a product:
    1. Curated v10 mapping check
    2. Handle reconciliation for cut-off titles
    3. Brand prefixing for generic bare nouns
    4. Dangling punctuation sanitization

    Returns (enriched_title, was_changed, method_used).
    """
    if not title:
        return "", False, "none"

    orig_title = title.strip()
    current = orig_title
    method = "none"

    # Strategy 1: v10 curated full_product_title
    if v10_lookup and url and url in v10_lookup:
        curated = v10_lookup[url]
        if curated and len(curated) > len(current) and curated != current:
            current = curated
            method = "v10_curated"

    # Strategy 2: Handle reconciliation for cut-off titles
    if handle and method != "v10_curated":
        reconciled = reconcile_title_from_handle(current, handle, brand or "")
        if reconciled != current:
            current = reconciled
            method = "handle_reconciled"

    # Strategy 3: Bare generic nouns lacking brand prefix
    clean_noun = re.sub(r'[^a-zA-Z0-9\s]', '', current).strip().lower()
    is_generic = clean_noun in GENERIC_TERMS or len(clean_noun.split()) <= 2
    if is_generic and brand and brand.lower() not in ('unknown', 'general', 'other', ''):
        if not is_brand_in_title(brand, current):
            current = f"{brand.strip()} {current}"
            method = "brand_prefixed" if method == "none" else f"{method}+brand_prefixed"

    # Strategy 4: Trailing punctuation & ellipsis cleanup
    cleaned = clean_trailing_punctuation(current)
    if cleaned != current:
        current = cleaned
        method = "punctuation_cleaned" if method == "none" else f"{method}+punctuation_cleaned"

    return current, current != orig_title, method

def run_title_enrichment(
    conn = None,
    dry_run: bool = False,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes database-wide product title enrichment across all records in SQLite,
    tracking progress and metrics via control tables.
    """
    own_task = False
    if task_id is None:
        task_id = create_task(
            task_type="title_enrichment",
            target_source="catalog",
            params={"dry_run": dry_run},
            auto_start=True
        )
        own_task = True

    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    v10_lookup = load_v10_title_lookup()

    try:
        cur = conn.cursor()
        cur.execute("SELECT id, store_id, brand, title, handle, product_url FROM products;")
        rows = cur.fetchall()
        total_prods = len(rows)

        update_task_progress(task_id, total_items=total_prods, current_stage="analyzing_titles", conn=conn)

        enriched_count = 0
        methods_breakdown = {
            "v10_curated": 0,
            "handle_reconciled": 0,
            "brand_prefixed": 0,
            "other": 0
        }
        sample_changes = []

        subtask_id = create_subtask(
            task_id=task_id,
            step_name="enrich_product_titles",
            step_index=1,
            total_steps=1,
            conn=conn
        )

        for idx, r in enumerate(rows, 1):
            pid = r["id"]
            orig_t = r["title"]
            h = r["handle"]
            b = r["brand"]
            u = r["product_url"]

            new_t, changed, method = enrich_product_title(orig_t, handle=h, brand=b, url=u, v10_lookup=v10_lookup)

            if changed:
                enriched_count += 1
                base_method = method.split("+")[0]
                if base_method in methods_breakdown:
                    methods_breakdown[base_method] += 1
                else:
                    methods_breakdown["other"] += 1

                if len(sample_changes) < 25:
                    sample_changes.append({
                        "id": pid,
                        "brand": b,
                        "before": orig_t,
                        "after": new_t,
                        "method": method
                    })

                if not dry_run:
                    cur.execute("""
                    UPDATE products
                    SET title = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """, (new_t, pid))

            if idx % 500 == 0 or idx == total_prods:
                update_task_progress(
                    task_id=task_id,
                    processed_items=idx,
                    successful_items=enriched_count,
                    current_stage=f"processed_{idx}_of_{total_prods}",
                    conn=conn
                )

        if not dry_run:
            conn.commit()

        complete_subtask(
            subtask_id=subtask_id,
            items_count=total_prods,
            items_succeeded=enriched_count,
            metadata=methods_breakdown,
            conn=conn
        )

        log_task_event(
            task_id=task_id,
            message=f"Title enrichment completed: {enriched_count}/{total_prods} titles upgraded (dry_run: {dry_run}).",
            level="INFO",
            context_data=methods_breakdown,
            conn=conn
        )

        if task_id:
            complete_task(
                task_id=task_id,
                metadata={
                    "total_products": total_prods,
                    "enriched_count": enriched_count,
                    "methods_breakdown": methods_breakdown,
                    "dry_run": dry_run
                },
                conn=conn
            )

        return {
            "status": "success",
            "task_id": task_id,
            "dry_run": dry_run,
            "total_products": total_prods,
            "enriched_count": enriched_count,
            "methods_breakdown": methods_breakdown,
            "sample_changes": sample_changes
        }
    except Exception as e:
        logger.error(f"Title enrichment failed: {e}")
        if task_id:
            fail_task(task_id, str(e), conn=conn)
        raise
    finally:
        if should_close:
            conn.close()
