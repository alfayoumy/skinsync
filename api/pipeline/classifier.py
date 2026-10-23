import re

NON_SKINCARE_KEYWORDS = [
    'pouch', 'bag', 'tote', 'candle', 't-shirt', 'mug', 'tumbler', 'cup',
    'gift card', 'voucher', 'keychain', 'sticker', 'box only', 'scrunchie'
]

def is_skincare_or_beauty(title: str, product_type: str, tags: list = None) -> bool:
    """
    Filters out accessories, merchandise, and non-cosmetic items.
    """
    t = f"{title} {product_type}".lower()
    for kw in NON_SKINCARE_KEYWORDS:
        if kw in t and not any(k in t for k in ['cream', 'serum', 'lotion', 'balm', 'wash']):
            return False
    return True

def classify_product(title: str, product_type: str = "") -> str:
    """
    Classifies a product into standard beauty categories.
    """
    t = f"{title} {product_type}".lower()

    if any(k in t for k in ['sunscreen', 'spf', 'sun block', 'sun cream', 'sun fluid', 'uv', 'solar']):
        return 'Sunscreen'
    if any(k in t for k in ['serum', 'ampoule', 'concentrate', 'booster serum', 'elixir']):
        return 'Serum'
    if any(k in t for k in ['cleanser', 'cleansing', 'face wash', 'wash', 'foaming', 'micellar', 'gel cleanser']):
        return 'Cleanser'
    if any(k in t for k in ['moisturizer', 'moisturiser', 'cream', 'lotion', 'hydrating gel', 'water gel', 'gel-cream', 'emulsion', 'day cream', 'night cream']):
        return 'Moisturizer'
    if any(k in t for k in ['toner', 'facial mist', 'mist', 'essence']):
        return 'Toner'
    if any(k in t for k in ['lip balm', 'lip therapy', 'lip oil', 'lip mask', 'lip care', 'lip scrub', 'lip butter', 'lipstick', 'lip gloss']):
        return 'Lip Care'
    if any(k in t for k in ['eye cream', 'eye contour', 'eye serum', 'eye gel', 'under eye']):
        return 'Eye Care'
    if any(k in t for k in ['sheet mask', 'clay mask', 'face mask', 'peel-off mask', 'mask']):
        return 'Mask'
    if any(k in t for k in ['peel', 'exfoliator', 'scrub', 'aha bha']):
        return 'Exfoliator'
    if any(k in t for k in ['shampoo', 'conditioner', 'hair mask', 'hair oil', 'scalp', 'hair serum']):
        return 'Hair Care'
    if any(k in t for k in ['body lotion', 'body butter', 'body wash', 'shower gel', 'body scrub', 'body oil']):
        return 'Body Care'
    if any(k in t for k in ['perfume', 'eau de parfum', 'eau de toilette', 'fragrance', 'body splash', 'body mist']):
        return 'Fragrance'
    if any(k in t for k in ['set', 'bundle', 'kit', 'routine']):
        return 'Set'

    return 'Other'
