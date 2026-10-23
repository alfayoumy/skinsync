"""
SkinSync Product Taxonomy & Skincare Classification Engine.
Precisely categorizes cosmetic and retail pharmacy products into:
1. Active Skincare & Body categories (Face, Eyes, Lips, Sun, Tanning, Body & Dermatological treatments)
2. Suppressed Non-Skincare categories (Supplements & Health, Medical Supplies, Makeup, Nail Care, Deodorant, Fragrance, Hair Care, Oral Care, Tools & Accessories)
"""

import re
import unicodedata
from typing import Tuple, List, Optional, Set

SKINCARE_CATEGORIES: Set[str] = {
    'Cleanser', 'Moisturizer', 'Serum', 'Sunscreen', 'Toner',
    'Exfoliator', 'Eye Care', 'Mask', 'Lip Care', 'Treatment',
    'Body Care', 'Tanning'
}

NON_SKINCARE_CATEGORIES: Set[str] = {
    'Deodorant', 'Fragrance', 'Hair Care', 'Makeup', 'Nail Care',
    'Supplements & Health', 'Medical Supplies',
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
    Deterministic rule-based cosmetic & pharmacy classifier.
    Returns:
        (category: str, is_skincare: bool)
    Where is_skincare is True for active skincare, body care, and tanning products.
    """
    raw = f"{brand} {title} {product_type}"
    # Normalize non-breaking spaces and separate letter-digit boundaries (e.g. 'Contour15ml' -> 'Contour 15 ml')
    raw = raw.replace('\xa0', ' ').replace('\u200b', ' ')
    raw = re.sub(r'([a-zA-Z])([0-9])', r'\1 \2', raw)
    raw = re.sub(r'([0-9])([a-zA-Z])', r'\1 \2', raw)
    t = " " + re.sub(r"[^a-z0-9%+\.]", " ", strip_accents(raw).lower()) + " "

    # =========================================================================
    # 1. NON-SKINCARE SUPPRESSED CATEGORIES (HIGHEST PRECEDENCE SAFEGUARDS)
    # =========================================================================

    # 1.1 Supplements & Health (Vitamins, Dietary, Pills, Minerals)
    if re.search(r"\b(capsules?|tablets?|caplets?|gummies|gummy|effervescent|sachets?|syrup|supplement|dietary\s*supplement|multivitamin|vitamins?|omega\s*\d*|fish\s*oil|collagen\s*(powder|capsules?|tablets?)|biotin|zinc|magnesium|calcium|iron\s*tablets?|protein|whey|creatine|glucosamine|probiotics?)\b", t):
        if not re.search(r"\b(serum|cream|gel|lotion|cleanser|mask|face|facial|topical)\b", t):
            return 'Supplements & Health', False

    # 1.2 Medical Supplies & First Aid
    if re.search(r"\b(thermometer|bandage|gauze|antiseptic|disinfectant|sanitizer|surgical|plaster|hot\s*water\s*bottle|heating\s*pad|sanitary\s*pad|tampons?|incontinence|compression\s*socks|test\s*strips|blood\s*pressure|lancets)\b", t):
        return 'Medical Supplies', False

    # 1.3 Oral Care
    if re.search(r"\b(toothpaste|toothbrush|mouthwash|dentifrice|oral\s*care|teeth\s*whitening|whitening\s*strips|dental\s*floss)\b", t):
        return 'Oral Care', False

    # 1.4 Tools & Accessories
    if re.search(r"\b(makeup\s*brush|beauty\s*blender|cotton\s*pads?|hair\s*dryer|straightener|curler|sharpener|tweezer|scissors|applicator|nail\s*file|nail\s*buffer|nail\s*clipper|nail\s*scissors|beter\b)\b", t):
        return 'Tools & Accessories', False

    # 1.5 Deodorants & Antiperspirants
    if re.search(r"\b(deodorant|antiperspirant|anti-perspirant|roll-on|roll\s*on|sweat\s*control|transpirant|spirial|\w*deo\b|\w*deo-|\w*deomineral|body\s*spray\s*deodorant)\b", t):
        return 'Deodorant', False

    # 1.6 Fragrances & Perfumery
    if re.search(r"\b(eau\s*de\s*parfum|eau\s*de\s*toilette|edp\b|edt\b|perfume|parfum|cologne|fragrance|body\s*splash|body\s*mist|scented\s*mist|oud\b|bakhoor)\b", t):
        return 'Fragrance', False

    # 1.7 Hair Care & Scalp Treatments
    if re.search(r"\b(shampoo|shampooing|conditioner|apres-shampooing|hair\s*mask|hair\s*oil|hair\s*serum|hair\s*cream|hair\s*mist|hair\s*lotion|hair\s*spray|hair\s*gel|scalp|hair\b|cheveux|capillaire|hairloss|anti-hair\s*loss|leave-in|hair\s*color|hair\s*dye|keratin\s*treatment|tonifying\s*gel)\b", t):
        return 'Hair Care', False

    # 1.8 Nail Care & Polish
    if re.search(r"\b(nail\s*polish|nail\s*lacquer|nail\s*enamel|nail\s*hardener|cuticle\s*oil|fake\s*nails|false\s*nails|nail\s*glue|nail\s*remover|nail\s*rescue|nail\s*care|nails\b|nail\b|aceton|yolo\b|topface\s*nail)\b", t):
        return 'Nail Care', False

    # 1.9 Makeup & Decorative Cosmetics
    if re.search(r"\b(mascara|eyeliner|eye\s*pencil|eyeshadow|eyebrow|brow\s*gel|brow\s*pencil|lipstick|lip\s*gloss|lip\s*tint|lip\s*liner|lip\s*pencil|lip\s*stain|lip\s*plump|foundation|concealer|blush|blusher|bronzer\s*palette|highlighter|compact\s*powder|setting\s*powder|loose\s*powder|fake\s*lashes|false\s*lashes|eyelashes|makeup\s*primer|makeup\s*fixer|contour\s*palette|sheglam|catrice|topface|kiss\s*lashes|liquid\s*matte|matte\s*liquid|tint\b)\b", t):
        return 'Makeup', False

    # 1.10 Baby Care (Diapers, Wipes)
    if re.search(r"\b(diaper|baby\s*wipes|nappy|baby\s*shampoo|baby\s*wash|baby\s*oil)\b", t):
        return 'Baby Care', False

    # 1.11 Multi-item Sets & Bundles
    if re.search(r"\b(set\b|bundle|kit\b|routine|pack\b)\b", t):
        return 'Set', False

    # =========================================================================
    # 2. ACTIVE SKINCARE, BODY & TANNING CATEGORIES (INCLUDED IN PHASE 1)
    # =========================================================================

    # 2.1 Tanning Products & After-Sun Care
    if re.search(r"\b(tan\b|tanning|self-tan|self\s*tan|fake\s*tan|bronzing|bronzer\s*(body|oil)|carrot\s*oil|carrot\s*spray|after\s*sun|after-sun|tan\s*extender|sun\s*oil|auto-bronzant)\b", t):
        return 'Tanning', True

    # 2.2 Sun Protection
    if re.search(r"\b(sunscreen|sun\s*screen|sun\s*block|sun\s*fluid|sun\s*cream|sun\s*gel|spf\s*\d+|spf\b|uvmune|photoderm|solaires?|sun\s*spray|sun\s*stick|sun\s*defense|acti-solaire)\b", t):
        return 'Sunscreen', True

    # 2.3 Eye Contour Care
    if re.search(r"\b(eye\s*cream|eye\s*contour|eye\s*serum|eye\s*gel|under\s*eye|eyes\b|yeux|contour\s*des\s*yeux|eye\s*patch|eye\s*patches|eye\s*care|cernes|dark\s*circle)\b", t):
        return 'Eye Care', True

    # 2.4 Lip Care (Restorative & Healing Balms)
    if re.search(r"\b(lip\s*balm|lip\s*care|lip\s*therapy|lip\s*butter|lip\s*oil|baume\s*levres|soin\s*des\s*levres|levres|lip\s*repair|lip\s*sleeping\s*mask|lip\s*moist|lip\s*polish)\b", t):
        return 'Lip Care', True

    # 2.5 Serums, Ampoules & Active Concentrates
    if re.search(r"\b(\w*serum\b|ampoule|booster|concentrate|elixir|face\s*oil|facial\s*oil|drops\b)\b", t):
        return 'Serum', True
    if re.search(r"\b(niacinamide|hyaluronic\s*acid|retinol|vitamin\s*c|salicylic\s*acid|glycolic\s*acid|azelaic\s*acid)\b", t) and re.search(r"\d+%", t):
        return 'Serum', True

    # 2.6 Cleansers & Face Washes
    if re.search(r"\b(cleanser|cleansing|face\s*wash|facial\s*wash|foam\s*wash|foaming\s*gel|gel\s*nettoyant|gel\s*moussant|gel\s*lavant|micellar|micellaire|eau\s*micellaire|moussant|wash\b|soap\b|savon|demaquillant|makeup\s*remover|balm\s*cleanser|oil\s*cleanser|aqua-med|teen\s*derm)\b", t):
        return 'Cleanser', True

    # 2.7 Exfoliators & Peels
    if re.search(r"\b(peel\w*|exfoliat\w*|scrub\w*|gommage|aha\s*bha|glycolic\s*peel|salicylic\s*peel|a\.?h\.?a|b\.?h\.?a)\b", t):
        return 'Exfoliator', True

    # 2.8 Toners, Essences & Thermal Water
    if re.search(r"\b(toner|tonique|lotion\s*tonique|facial\s*mist|thermal\s*water|eau\s*thermale|thermal\s*spring\s*water|essence)\b", t):
        return 'Toner', True

    # 2.9 Facial Masks
    if re.search(r"\b(sheet\s*mask|face\s*mask|clay\s*mask|stick\s*mask|masque|sleeping\s*mask|peel-off\s*mask|mask\b)\b", t):
        return 'Mask', True

    # 2.10 Targeted Dermatological Treatments
    if re.search(r"\b(spot\s*treatment|acne|blemish|anti-blemish|soothing\s*gel|repair\s*gel|cicavit|cica-gel|blemish\s*control|clari-spot|dermopure|mattfying\s*fluid|pore\s*minimizer)\b", t):
        return 'Treatment', True

    # 2.11 Body Care (Body lotions, milks, washes, hand & foot creams, dry oils)
    if re.search(r"\b(shower\s*gel|body\s*wash|gel\s*douche|body\s*scrub|hand\s*cream|foot\s*cream|hand\s*(&\s*)?nail|cream\s*mains|creme\s*mains|mains\b|pieds\b|feet\s*cream|heel\s*cream|cracked\s*heel|cellulite|stretch\s*mark|anti-cellulite|intimate\s*wash|gyn-phy|body\s*butter|body\s*lotion|body\s*milk|lait\s*corporel|body\s*oil|dry\s*oil|huile\s*prodigieuse|corps\b|body\b|lait\b)\b", t):
        return 'Body Care', True

    # 2.12 Moisturizers, Emulsions & Barrier Creams
    if re.search(r"\b(moisturizer|moisturiser|cream|creme|lotion|gel-cream|water\s*gel|hydro\s*gel|dry\s*skin\s*gel|hydra|barrier|cica|balm|baume|emulsion|day\s*cream|night\s*cream|even\s*pigment\s*perfector\s*night|creme\s*de\s*jour|creme\s*de\s*nuit|repair\s*cream|emollient|urelia)\b", t):
        return 'Moisturizer', True

    # Default fallback: Non-skincare
    return 'Other Non-Skincare', False

def classify_product(title: str, brand_or_type: str = "", product_type: str = "") -> str:
    """
    Convenience wrapper returning category name only for existing pipeline compatibility.
    """
    if product_type:
        brand = brand_or_type
    else:
        brand = ""
        product_type = brand_or_type

    cat, _ = classify_and_tag(title, brand=brand, product_type=product_type)
    return cat

def is_skincare_product(category: str, title: str = "", brand: str = "") -> bool:
    """
    Returns True if the category and title represent active skincare, body care, or tanning.
    """
    if category in SKINCARE_CATEGORIES:
        return True
    _, is_skin = classify_and_tag(title, brand=brand)
    return is_skin
