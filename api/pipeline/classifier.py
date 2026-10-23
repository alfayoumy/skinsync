"""
SkinSync Product Taxonomy & Skincare Classification Engine.
Precisely categorizes cosmetic and retail pharmacy products into:
1. Active Skincare categories (Face, Eyes, Lips, Sun, Dermatological treatments)
2. Suppressed Non-Skincare categories (Deodorant, Fragrance, Hair Care, Makeup, Body Care, Oral Care, Tools & Accessories)
"""

import re
import unicodedata
from typing import Tuple, List, Optional, Set

SKINCARE_CATEGORIES: Set[str] = {
    'Cleanser', 'Moisturizer', 'Serum', 'Sunscreen', 'Toner',
    'Exfoliator', 'Eye Care', 'Mask', 'Lip Care', 'Treatment'
}

NON_SKINCARE_CATEGORIES: Set[str] = {
    'Deodorant', 'Fragrance', 'Hair Care', 'Makeup', 'Body Care',
    'Oral Care', 'Tools & Accessories', 'Baby Care', 'Set', 'Other Non-Skincare'
}

NON_SKINCARE_MERCH_KEYWORDS = [
    'pouch', 'tote', 'candle', 't-shirt', 'mug', 'tumbler', 'cup',
    'gift card', 'voucher', 'keychain', 'sticker', 'box only', 'scrunchie'
]

def strip_accents(s: str) -> str:
    """Removes unicode diacritical marks (e.g. 'crème' -> 'creme')."""
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def is_skincare_or_beauty(title: str, product_type: str = "", tags: Optional[List[str]] = None) -> bool:
    """
    Filters out non-cosmetic items, accessories, gift packaging, and merchandise.
    """
    t = f"{title} {product_type}".lower()
    for kw in NON_SKINCARE_MERCH_KEYWORDS:
        if kw in t and not any(k in t for k in ['cream', 'serum', 'lotion', 'balm', 'wash']):
            return False
    return True

def classify_and_tag(title: str, brand: str = "", product_type: str = "") -> Tuple[str, bool]:
    """
    Deterministic rule-based cosmetic classifier.
    Returns:
        (category: str, is_skincare: bool)
    Where is_skincare is True ONLY for core facial skincare products.
    """
    raw = f"{brand} {title} {product_type}"
    t = " " + re.sub(r"[^a-z0-9%+\.]", " ", strip_accents(raw).lower()) + " "

    # =========================================================================
    # 1. NON-SKINCARE CATEGORIES (HIGHEST PRECEDENCE SAFEGUARD)
    # =========================================================================

    # 1.1 Oral Care
    if re.search(r"\b(toothpaste|toothbrush|mouthwash|dentifrice|oral\s*care|teeth\s*whitening|whitening\s*strips|dental\s*floss)\b", t):
        return 'Oral Care', False

    # 1.2 Tools & Accessories
    if re.search(r"\b(makeup\s*brush|beauty\s*blender|cotton\s*pad|cotton\s*pads|tweezers|hair\s*dryer|straightener|curler|sharpener|tweezer|scissors|applicator)\b", t):
        return 'Tools & Accessories', False

    # 1.3 Deodorant & Antiperspirants
    if re.search(r"\b(deodorant|antiperspirant|anti-perspirant|roll-on|roll\s*on|sweat\s*control|transpirant|spirial|deo\b|deo-|body\s*spray\s*deodorant)\b", t):
        return 'Deodorant', False

    # 1.4 Fragrances & Perfumery
    if re.search(r"\b(eau\s*de\s*parfum|eau\s*de\s*toilette|edp\b|edt\b|perfume|parfum|cologne|fragrance|body\s*splash|body\s*mist|scented\s*mist|oud\b|bakhoor)\b", t):
        return 'Fragrance', False

    # 1.5 Hair Care & Scalp Treatments
    if re.search(r"\b(shampoo|shampooing|conditioner|apres-shampooing|hair\s*mask|hair\s*oil|hair\s*serum|hair\s*cream|hair\s*mist|hair\s*lotion|hair\s*spray|hair\s*gel|scalp|hair\b|cheveux|capillaire|hairloss|anti-hair\s*loss|leave-in|hair\s*color|hair\s*dye|keratin\s*treatment|tonifying\s*gel)\b", t):
        return 'Hair Care', False

    # 1.6 Makeup & Decorative Cosmetics
    if re.search(r"\b(mascara|eyeliner|eye\s*pencil|eyeshadow|eyebrow|brow\s*gel|brow\s*pencil|lipstick|lip\s*gloss|lip\s*tint|lip\s*liner|lip\s*pencil|foundation|concealer|blush|blusher|bronzer|highlighter|compact\s*powder|setting\s*powder|loose\s*powder|nail\s*polish|nail\s*lacquer|fake\s*nails|false\s*lashes|eyelashes|primer\b|makeup\s*fixer|contour\s*palette)\b", t):
        return 'Makeup', False

    # 1.7 Dedicated Body, Hand & Foot Care
    if re.search(r"\b(shower\s*gel|body\s*wash|gel\s*douche|body\s*scrub|hand\s*cream|foot\s*cream|hand\s*(&\s*)?nail|cream\s*mains|creme\s*mains|feet\s*cream|heel\s*cream|cellulite|stretch\s*mark|anti-cellulite|intimate\s*wash|body\s*butter|body\s*lotion|body\s*milk|lait\s*corporel|body\s*oil|corps\b)\b", t):
        # Allow multi-purpose dermatological emulsions that treat both face and body
        if not re.search(r"\b(face\s*and\s*body|visage\s*et\s*corps|face\s*&\s*body)\b", t):
            return 'Body Care', False

    # 1.8 Baby Care
    if re.search(r"\b(diaper|baby\s*wipes|nappy|baby\s*shampoo|baby\s*wash|baby\s*oil)\b", t):
        return 'Baby Care', False

    # =========================================================================
    # 2. CORE SKINCARE CATEGORIES (FACE, EYES, LIPS, SUN, DERMATOLOGICAL)
    # =========================================================================

    # 2.1 Sun Protection
    if re.search(r"\b(sunscreen|sun\s*screen|sun\s*block|sun\s*fluid|sun\s*cream|sun\s*gel|spf\s*\d+|spf\b|uvmune|photoderm|solaires?|sun\s*spray|sun\s*stick)\b", t):
        return 'Sunscreen', True

    # 2.2 Eye Contour Care
    if re.search(r"\b(eye\s*cream|eye\s*contour|eye\s*serum|eye\s*gel|under\s*eye|eyes\b|yeux|contour\s*des\s*yeux|eye\s*patch|eye\s*patches)\b", t):
        return 'Eye Care', True

    # 2.3 Lip Care (Restorative & Healing Balms)
    if re.search(r"\b(lip\s*balm|lip\s*care|lip\s*therapy|lip\s*butter|lip\s*oil|baume\s*levres|soin\s*des\s*levres|levres|lip\s*repair|lip\s*sleeping\s*mask)\b", t):
        return 'Lip Care', True

    # 2.4 Serums, Ampoules & Boosters
    if re.search(r"\b(serum|ampoule|booster|concentrate|elixir|face\s*oil|facial\s*oil|drop\b|drops\b)\b", t):
        return 'Serum', True

    # 2.5 Cleansers & Face Washes
    if re.search(r"\b(cleanser|cleansing|face\s*wash|facial\s*wash|foam\s*wash|foaming\s*gel|gel\s*nettoyant|gel\s*moussant|micellar|micellaire|eau\s*micellaire|moussant|wash\b|soap\b|savon|makeup\s*remover|balm\s*cleanser|oil\s*cleanser)\b", t):
        return 'Cleanser', True

    # 2.6 Exfoliators & Peels
    if re.search(r"\b(peel|peeling|exfoliat|scrub|gommage|aha\s*bha|glycolic\s*acid|lactic\s*acid|salicylic\s*peel)\b", t):
        return 'Exfoliator', True

    # 2.7 Toners, Essences & Thermal Water
    if re.search(r"\b(toner|tonique|lotion\s*tonique|facial\s*mist|thermal\s*water|eau\s*thermale|thermal\s*spring\s*water|essence)\b", t):
        return 'Toner', True

    # 2.8 Facial Masks
    if re.search(r"\b(sheet\s*mask|face\s*mask|clay\s*mask|masque|sleeping\s*mask|peel-off\s*mask)\b", t):
        return 'Mask', True

    # 2.9 Targeted Dermatological Treatments
    if re.search(r"\b(spot\s*treatment|acne|blemish|anti-blemish|soothing\s*gel|repair\s*gel|cicavit|cica-gel|blemish\s*control|blemish\s*gel)\b", t):
        return 'Treatment', True

    # 2.10 Moisturizers, Emulsions & Barrier Creams
    if re.search(r"\b(moisturizer|moisturiser|cream|creme|lotion|gel-cream|water\s*gel|hydra|barrier|cica|balm|baume|emulsion|day\s*cream|night\s*cream|creme\s*de\s*jour|creme\s*de\s*nuit|moisturizing\s*lotion|repair\s*cream)\b", t):
        return 'Moisturizer', True

    # 2.11 Multi-item Sets & Bundles
    if re.search(r"\b(set\b|bundle|kit\b|routine|pack\b)\b", t):
        return 'Set', False

    # Default fallback: Non-skincare
    return 'Other Non-Skincare', False

def classify_product(title: str, brand_or_type: str = "", product_type: str = "") -> str:
    """
    Convenience wrapper returning category name only for existing pipeline compatibility.
    """
    # Handle callers passing (title, product_type) vs (title, brand, product_type)
    if product_type:
        brand = brand_or_type
    else:
        brand = ""
        product_type = brand_or_type

    cat, _ = classify_and_tag(title, brand=brand, product_type=product_type)
    return cat

def is_skincare_product(category: str, title: str = "", brand: str = "") -> bool:
    """
    Returns True if the category and title represent active facial skincare.
    """
    if category in SKINCARE_CATEGORIES:
        return True
    _, is_skin = classify_and_tag(title, brand=brand)
    return is_skin
