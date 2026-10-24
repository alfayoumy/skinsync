"""Evidence-based taxonomy with compatibility labels and reviewable uncertainty."""
from dataclasses import dataclass, asdict, field
import re
import unicodedata

SKINCARE = {'Cleanser','Moisturizer','Serum','Sunscreen','Toner','Exfoliator','Eye Skincare',
            'Eye Care','Mask','Lip Care','Treatment','Body Care','Tanning'}
CATEGORY_PATTERNS = [
 ('Sunscreen',r'\b(sunscreen|sun\s*(?:screen|block|fluid|cream|stick)|spf\s*\d+|uvmune|photoderm)\b|واقي الشمس'),
 ('Eye Skincare',r'\b(?:eye|under eye|yeux|dark circles?)\s*(?:cream|serum|gel|balm|patch|care|repair|contour)|\bcaffeine solution\b'),
 ('Lip Care',r'\b(?:lip (?:balm|oil|care|therapy|butter|repair|sleeping mask)|baume levres)\b'),
 ('Body Care',r'\b(?:body (?:wash|lotion|cream|butter|oil|scrub|milk)|hand cream|foot cream|shower gel|huile prodigieuse)\b'),
 ('Cleanser',r'\b(?:cleanser|cleansing|face wash|facial wash|foaming gel|micellar|micellaire|makeup remover|gel nettoyant)\b|غسول'),
 ('Toner',r'\b(?:toner|tonique|essence|thermal water|facial mist)\b|تونر'),
 ('Mask',r'\b(?:sheet mask|face mask|clay mask|sleeping mask|masque)\b'),
 ('Exfoliator',r'\b(?:peeling|peel|exfoliator|scrub|gommage|exfoliating solution)\b'),
 ('Serum',r'\b(?:serum|ampoule|booster|concentrate|face oil|facial oil)\b|سيروم'),
 ('Treatment',r'\b(?:spot treatment|acne treatment|blemish control|repair gel)\b'),
 ('Moisturizer',r'\b(?:moisturiz\w*|moisturis\w*|cream|creme|lotion|emulsion|barrier balm|water gel|emollient|hydrator)\b|مرطب'),
 ('Tanning',r'\b(?:tanning|self tan|after sun|tan extender)\b')]
HARD_EXCLUSIONS = [
 ('Oral Care',r'\b(?:toothpaste|toothbrush|mouthwash|dental floss)\b'),
 ('Medical Supplies',r'\b(?:thermometer|gauze|bandage|test strips|blood pressure|surgical|sanitary pads?)\b'),
 ('Supplements & Health',r'\b(?:dietary supplement|tablets?|capsules?|gummies|multivitamin|fish oil|protein powder)\b'),
 ('Tools & Accessories',r'\b(?:makeup brush|hair dryer|tweezers?|gift card|empty packaging|box only|pouch|tote)\b'),
 ('Hair Care',r'\b(?:shampoo|conditioner|hair (?:serum|oil|cream|mask|spray|color)|scalp|leave in|kerastase|professionnel|frizz|hairloss|dandruff)\b'),
 ('Deodorant',r'\b(?:deodorant|antiperspirant|roll on)\b'),
 ('Fragrance',r'\b(?:eau de parfum|eau de toilette|perfume|cologne|body splash|body mist|bakhoor|oud)\b'),
 ('Nail Care',r'\b(?:nail polish|nail lacquer|nail hardener|cuticle oil|nail care)\b'),
 ('Makeup',r'\b(?:mascara|eyeliner|eyeshadow|lipstick|foundation|concealer|blush|setting powder|nail polish|lash serum|eyebrow)\b'),
 ('Baby Care',r'\b(?:diapers?|nappies|baby wipes)\b')]


def normalize(value):
    value=unicodedata.normalize('NFKC',value or '').casefold()
    value=''.join(c for c in unicodedata.normalize('NFD',value) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w%+.]',' ',value).split())


@dataclass
class Classification:
    vertical: str
    primary_category: str
    subcategory: str
    application_area: str
    formulation_type: str | None
    usage_type: str
    features: list
    skin_concerns: list
    is_skincare: bool
    classification_confidence: float
    classification_status: str
    classification_source: str = 'evidence_rules_v1'
    evidence: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    def to_dict(self): return asdict(self)


def classify_product_attributes(title, brand='', product_type='', description='', breadcrumbs=None, manufacturer_type=''):
    # Negative features are not product functions.
    clean_title=normalize(title)
    clean_title=re.sub(r'\b(?:fragrance|parfum|perfume) free\b','unscented',clean_title)
    clean_title=re.sub(r'\bmake ?up remover\b','cleanser',clean_title)
    sources=[('title',clean_title,4),('manufacturer',normalize(manufacturer_type),5),
             ('retailer_category',normalize(product_type),1),('breadcrumbs',normalize(' '.join(breadcrumbs or [])),1),
             ('description',normalize(description),.25)]
    features=[]
    for term, pattern in [('fragrance_free',r'\b(?:unscented|fragrance free|parfum free)\b'),('tinted',r'\btinted\b'),
                          ('baby',r'\bbaby\b'),('exfoliating',r'\b(?:exfoliat\w*|aha|bha)\b')]:
        if re.search(pattern,clean_title): features.append(term)
    spf=re.search(r'\bspf\s*(\d+)\b',clean_title)
    if spf: features.append('SPF '+spf[1])
    area='eye' if re.search(r'\b(?:eye|yeux|under eye)\b',clean_title) else ('lip' if re.search(r'\blip\b',clean_title) else
        'body' if re.search(r'\b(?:body|hand|foot|shower)\b',clean_title) else 'face')
    vehicle=next((v for v in ('fluid','cream','lotion','serum','balm','oil','gel','foam','mist','stick','patch')
                  if re.search(r'\b'+v+r'\b',clean_title)),None)
    concerns=[name for name,pattern in [('UV protection',r'spf|sunscreen'),('acne',r'acne|blemish'),
        ('pigmentation',r'pigment|brighten|dark spots'),('hydration',r'hydrat|moistur'),('barrier repair',r'barrier|cica')]
        if re.search(pattern,clean_title)]
    evidence=[]
    # Explicit product types in the title take precedence over incidental description words.
    excluded=next(((cat,pat) for cat,pat in HARD_EXCLUSIONS if re.search(pat,clean_title)),None)
    if excluded and excluded[0]=='Supplements & Health' and re.search(r'\b(?:serum|cream|topical|face|skin)\b',clean_title):
        excluded=None  # topical capsules are evaluated by product function below
    bundle=bool(re.search(r'\b(?:gift set|routine set|kit|bundle|set of)\b',clean_title))
    if bundle: excluded=('Set','bundle')
    if excluded:
        category=excluded[0]
        return Classification('other_beauty' if category in {'Makeup','Fragrance','Hair Care','Deodorant'} else 'non_skincare',
            category,category,area,vehicle,'not_applicable',features,concerns,False,.99,'excluded',evidence=[{'source':'title','match':excluded[1]}])
    scores={}
    for category, pattern in CATEGORY_PATTERNS:
        for source,text,weight in sources:
            match=re.search(pattern,text)
            if match:
                scores[category]=scores.get(category,0)+weight
                evidence.append({'source':source,'category':category,'match':match[0],'weight':weight})
    # Contextual functions resolve legitimate overlaps, independent of regex order.
    title_matches={e['category'] for e in evidence if e['source']=='title'}
    priorities=['Sunscreen','Eye Skincare','Lip Care','Body Care','Cleanser','Toner','Mask','Exfoliator','Serum','Treatment','Moisturizer','Tanning']
    category=next((c for c in priorities if c in title_matches),None)
    if category is None and scores:
        category=max(scores,key=scores.get)
    conflicts=[]
    if len(title_matches & {'Cleanser','Sunscreen','Toner'})>1:
        conflicts.append('Conflicting product functions in title')
    if category is None:
        # Preserve established specialized language handling; uncertainty remains explicit.
        from pipeline.classifier import _legacy_classify_and_tag
        legacy,eligible=_legacy_classify_and_tag(title,brand,product_type)
        category=legacy
        if legacy=='Other Non-Skincare':
            return Classification('unresolved',legacy,'unresolved',area,vehicle,'unknown',features,concerns,False,0,'review',evidence=evidence)
        if not eligible:
            return Classification('other_beauty',legacy,legacy,area,vehicle,'not_applicable',features,concerns,False,.9,'excluded',evidence=evidence)
    eligible=category in SKINCARE
    confidence=.96 if title_matches and not conflicts else (.8 if not conflicts else .5)
    status='auto_approved' if title_matches and not conflicts else 'review'
    usage='rinse_off' if category=='Cleanser' or 'peel' in clean_title else 'leave_on'
    return Classification('skincare' if eligible else 'other_beauty',category,f'{area} {category.lower()}',area,
        vehicle,usage,features,concerns,eligible,confidence,status,evidence=evidence,conflicts=conflicts)


def seed_taxonomy(conn):
    for category in sorted(SKINCARE | {c for c,_ in HARD_EXCLUSIONS} | {'Set','Other Non-Skincare'}):
        key=category.lower().replace(' ','_')
        conn.execute('INSERT OR IGNORE INTO product_taxonomy(id,label,vertical,parent_id,is_skincare) VALUES (?,?,?,?,?)',
            (key,category,'skincare' if category in SKINCARE else 'other_beauty',None,int(category in SKINCARE)))


def reconcile_canonical_classification(conn, canonical_id, classification, reviewer=None):
    import json
    row=conn.execute('SELECT classification_status FROM canonical_products WHERE id=?',(canonical_id,)).fetchone()
    if not row: raise ValueError('Canonical product not found.')
    if row[0]=='approved' and not reviewer:
        return False
    if not reviewer and classification.classification_status!='auto_approved':
        return False
    conn.execute('UPDATE canonical_products SET category=?,is_skincare=?,classification=?,classification_status=? WHERE id=?',
        (classification.primary_category,int(classification.is_skincare),json.dumps(classification.to_dict()),
         'approved' if reviewer else 'auto_approved',canonical_id))
    return True
