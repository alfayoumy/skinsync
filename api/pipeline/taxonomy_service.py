"""Evidence-based taxonomy with compatibility labels and reviewable uncertainty."""
from dataclasses import dataclass, asdict, field
import re
import unicodedata
import html

SKINCARE = {'Cleanser','Moisturizer','Serum','Sunscreen','Toner','Exfoliator','Eye Skincare',
            'Eye Care','Mask','Lip Care','Treatment','Body Care','Tanning'}
CATEGORY_PATTERNS = [
 ('Sunscreen',r'\b(sunscreen|sun\s*(?:screen|block|fluid|cream|stick)|spf\s*\d+|uvmune|photoderm)\b|واقي الشمس'),
 ('Eye Skincare',r'\b(?:eyes?|under eye|yeux|dark circles?)\s*(?:cream|serum|gel|balm|patch\w*|care|repair|contour|fortifier|discoloration treatment)|\bcaffeine solution\b|\bcontour (?:des )?yeux\b'),
 ('Lip Care',r'\b(?:lips? (?:balm|oil|care|therapy|treatment|serum|cream|butter|repair|moist\w*|mask|sleeping mask)|baume levres|cica lips)\b'),
 ('Body Care',r'\b(?:body (?:(?:musk|white|rich|repairing|barrier|protective|intensive) ){0,3}(?:wash|soap|lotion|cream|butter|balm|oil|scrub|milk|gel)|hands? (?:(?:and )?nails? )?(?:(?:repairing|barrier|protective|intensive|rich) ){0,3}(?:cream|wash|balm|serum|lotion)|foot (?:cream|scrub|balm|lotion)|baby oil|(?:diaper|nappy)(?: rash)? (?:cream|balm|ointment)|shower (?:gel|foam)|shaving (?:gel|foam|cream)|gel douche|bath (?:foam|bomb|wash)|intimate (?:wash|hygiene)|lait corporel|huile prodigieuse)\b'),
 ('Cleanser',r'\b(?:soap|cleanser|cleansing|face wash|facial wash|(?:creamy|cream) wash|gel wash|foaming (?:gel|cream|creme|wash)|micellar|micellaire|makeup remover|gel (?:nettoyant|moussant|lavant)|glycerin soap)\b|غسول'),
 ('Toner',r'\b(?:toner|tonique|essence|thermal (?:spring )?water|eau thermale|facial mist)\b|تونر'),
 ('Mask',r'\b(?:mask|masque|sleeping pack)\b'),
 ('Exfoliator',r'\b(?:peeling|peel|exfoliator|scrub|gommage|exfoliating solution)\b'),
 ('Serum',r'\b(?:serum|ampoule|booster|concentrate|face oil|facial oil)\b|سيروم'),
 ('Treatment',r'\b(?:spot treatment|acne treatment|anti blemish (?:care|treatment)|blemish control|pore minimizer|acne patches|pimple patches|repair gel)\b'),
 ('Moisturizer',r'\b(?:moisturiz\w*|moisturis\w*|cream|creme|lotion|emulsion|balm|healing jelly|water gel|hydro gel|emollient|hydrator)\b|مرطب'),
 ('Tanning',r'\b(?:tanning|self tan|after sun|tan extender)\b')]
HARD_EXCLUSIONS = [
 ('Oral Care',r'\b(?:toothpaste|toothbrush|mouthwash|dental floss)\b'),
 ('Medical Supplies',r'\b(?:thermometer|gauze|bandage|test strips|blood pressure|surgical|sanitary pads?)\b'),
 ('Supplements & Health',r'\b(?:dietary supplement|tablets?|capsules?|gummies|multivitamin|fish oil|protein powder)\b'),
 ('Tools & Accessories',r'\b(?:makeup brush|hair dryer|tweezers?|gift card|empty packaging|box only|pouch|tote|powder puff|sharpener|brush holder|lash(?:es)? storage case)\b'),
 ('Hair Care',r'\b(?:shampoo|conditioner|hair (?:serum|oil|cream|mask|spray|color|tonic|lotion|treatment|mist|gel)|scalp|leave in|kerastase|professionnel|frizz|hairloss|dandruff)\b'),
 ('Deodorant',r'\b(?:deodorant|deodrant|anti ?perspirant|deo|roll on)\b'),
 ('Fragrance',r'\b(?:eau de parfum|eau de toilette|perfume|cologne|body splash|body mist|bakhoor|oud)\b'),
 ('Nail Care',r'\b(?:nail polish|nail lacquer|nail hardener|cuticle oil|nail care)\b'),
 ('Makeup',r'\b(?:mascara|eyeliner|eyeshadow|lipstick|lip (?:cheek )?tint|foundation|concealer|blush\w*|setting powder|nail polish|lash serum|eyebrow)\b'),
 ('Baby Care',r'\b(?:diapers?|nappies|baby wipes)\b')]

# Explicit functions and merchandise previously fell through to description
# keywords (or "Other Non-Skincare"). Keep these bounded to product types;
# broad words such as "pad", "oil", "water" or "gel" are not enough.
HARD_EXCLUSIONS.extend([
 ('Makeup',r'\b(?:brow (?:(?:fixing|lifting|lift|freeze) ){0,3}(?:gel|glue|wax|mascara|pen)|(?:kajal|kohl)(?: kohl)? pencil|eye brightening pen|highlighter|skin tint|bronzing stick|make ?up (?:fixing|setting) spray|false lash(?:es)?|glamour lashes|(?:ink|pro) liner|lipliner|(?:color|colour) changing lip (?:and )?cheek oil|lip (?:and )?cheek (?:cream|tint|stick)|cheek (?:and )?lip cream|lip (?:mousse|marker|pen|pencil|stain|blur))\b'),
 ('Tools & Accessories',r'\b(?:sunglasses|(?:water )?teethers?|soother (?:holder|clip)|foot file|(?:writing|activity) (?:german |arabic |french )?book)\b'),
 ('Medical Supplies',r'\b(?:ankle support|(?:sports )?braces|pantyliners?)\b'),
 ('Fragrance',r'\breed diffusers?\b'),
 ('Makeup',r'\b(?:lip liner|lip ?gloss|brow pomade|brow pencil|eye pencil|color corrector|setting spray|bb (?:cream|skin editor))\b'),
 ('Hair Care',r'\b(?:for (?:(?:the|damaged|dry|curly|colored) )?hair|hair transplant|hair (?:fall|loss|growth)|hair ampoules?|hairburst|hairoots|hairvogine)\b'),
 ('Makeup',r'\bprimer\b'),
 ('Tools & Accessories',r'\b(?:straw(?: \w+){0,2} cup|spout bottle|(?:nursing|baby support) pillow|blades for (?:men|women)|shaving blades|loofah)\b'),
 ('Medical Supplies',r'\b(?:solution for injection|pre filled pen|sanitary pads|panty liners)\b'),
 ('Tools & Accessories',r'\b(?:loofah|razors?|razor blades?|stroller|feeding pillow|sippy cup|straw cup|baby bottle|backpack harness|aromatic candle)\b'),
 ('Tools & Accessories',r'\b(?:cotton (?:ear )?buds|cotton swabs|bathroom scale|training cup|sport cup|drying rack|derma ?roller|skin care tool|candles?)\b'),
 ('Medical Supplies',r'\b(?:condoms?|panty ?liners)\b'),
 ('Fragrance',r'\bincense (?:cones|sticks)\b'),
 ('Nail Care',r'\bnails? (?:cream|balm|oil|treatment|lotion|serum|booster|rescue|growth|guard)\b'),
 ('Makeup',r'\b(?:eyelash(?:es)?|lash (?:and )?brow|browlash|brow serum|lash(?:es)? (?:growth |treatment )?serum)\b'),
 ('Fragrance',r'\b(?:edp|edt|air freshener|linen spray|wardrobe scented bar)\b'),
 ('Medical Supplies',r'\b(?:kwikpen|injection pen|hot cold.*cushion)\b'),
])

# Exact retailer product types are usable evidence when the title is merely a
# shade or product-line name. Department labels such as "Skin Care", "Pharmacy"
# and "Mother & Baby Care" deliberately remain unresolved.
EXPLICIT_PRODUCT_TYPES = {
    'face serum':'Serum','facial serum':'Serum','serum':'Serum',
    'face cream':'Moisturizer','moisturizer':'Moisturizer','moisturiser':'Moisturizer',
    'face wash':'Cleanser','facial cleanser':'Cleanser','cleanser':'Cleanser',
    'face mask':'Mask','sheet mask':'Mask','toner':'Toner','sunscreen':'Sunscreen',
    'lip balm':'Lip Care','body oil':'Body Care','body cream':'Body Care',
    'body lotion':'Body Care','hand wash':'Body Care',
    'makeup':'Makeup','lipstick':'Makeup','lip gloss':'Makeup',
    'lip gloss and lip tint':'Makeup','lip tint':'Makeup',
    'hair care':'Hair Care','hair oil and serum':'Hair Care','hair color':'Hair Care',
    'perfumes fragrances':'Fragrance','perfume':'Fragrance','fragrance':'Fragrance',
    'candle':'Tools & Accessories','brush comb':'Tools & Accessories',
    'nursing feeding':'Baby Care','on the go gear':'Tools & Accessories',
    'skin bundle':'Set','bundle':'Set','skincare set':'Set',
    'skincare gifts and sets':'Set','men bundle':'Set','body bundle':'Set',
    'body gifts and sets':'Set','makeup gifts and sets':'Set','set care':'Set',
    'shower gel body wash':'Body Care','body lotion and oil':'Body Care',
    'hand and body soap':'Body Care','bath bomb salt':'Body Care',
    'face soap':'Cleanser','cleanser and face soap':'Cleanser',
    'day and night cream':'Moisturizer','spf':'Sunscreen','lip care':'Lip Care',
    'eye care':'Eye Skincare','eye cream':'Eye Skincare','eye serum':'Eye Skincare',
    'eye patches':'Eye Skincare','eye patch':'Eye Skincare','eye treatment':'Eye Skincare',
    'acne treatment':'Treatment',
    'hair oil':'Hair Care','hair ampoule':'Hair Care','styling gel':'Hair Care',
    'hair serum':'Hair Care','hair cream':'Hair Care','styling cream':'Hair Care',
    'hair scalp treatment':'Hair Care','hair treatment':'Hair Care','hair styling':'Hair Care',
    'hair styling product':'Hair Care','hair dye':'Hair Care','hair growth':'Hair Care',
    'conditioner':'Hair Care','shampoo':'Hair Care','leave in conditioner':'Hair Care',
    'leave in hair treatment':'Hair Care','kids hair cream':'Hair Care',
    'men styling gel':'Hair Care','men shampoo':'Hair Care','hair leave in':'Hair Care',
    'hair mask':'Hair Care','hair conditioner':'Hair Care','hair spray':'Hair Care',
    'highlighter contour':'Makeup','highlighter':'Makeup','contour':'Makeup',
    'primer':'Makeup','face primer':'Makeup','powder':'Makeup','face powder':'Makeup',
    'blush':'Makeup','foundation':'Makeup','bronzer':'Makeup','lip plumper':'Makeup',
    'skin tool':'Tools & Accessories','body tool':'Tools & Accessories',
    'diffuser':'Fragrance','mist and spray for him':'Fragrance',
    'diapering potty':'Baby Care','first aid pads compresses':'Medical Supplies',
    'face exfoliator':'Exfoliator','face scrub and exfoliators':'Exfoliator',
    'face mist':'Toner','facial mist':'Toner','face mist and spray':'Toner',
    'face oil':'Serum','face treatment':'Treatment','acne patch':'Treatment',
    'anti scar':'Treatment','body gel':'Body Care','intimate wash':'Body Care',
    'shower gel':'Body Care','tanning oil':'Tanning',
    'sunscreen cream lotion':'Sunscreen',
    'makeup tool':'Tools & Accessories','makeup tools and brushes':'Tools & Accessories',
    'makeup bag':'Tools & Accessories',
    'perfume for him':'Fragrance','unisex perfume':'Fragrance',
    'perfume set for him':'Set',
    'makeup spray':'Makeup','setting spray':'Makeup','face palette':'Makeup',
    'lip liner':'Makeup','nail polish':'Nail Care',
    'deodorant spray stick':'Deodorant','men beard oil':'Hair Care',
    'scalp treatment and vials':'Hair Care',
}


def description_declaration(description,brand=''):
    """Use the first declared product type, never later usage/ingredient prose."""
    if not description:return None
    text=re.sub(r'<(script|style)\b[^>]*>.*?</\1>',' ',description,flags=re.I|re.S)
    text=html.unescape(re.sub(r'<[^>]+>',' ',text))
    text=' '.join(text.split())
    text=re.split(r'(?<=[a-zA-Z])[.!?](?:\s|$)',text,maxsplit=1)[0]
    text=' '.join(text.split()[:32])
    clean=normalize(text)
    if re.match(r'^set contains\b',clean):
        return classify_product_attributes('Routine Set',brand),'set contains'
    if re.match(r'^(?:how to|directions|apply|use |ingredients|contains|enriched with|formulated with|benefits)',clean):return None
    matches=[]
    for category,pattern in CATEGORY_PATTERNS+HARD_EXCLUSIONS:
        for found in re.finditer(pattern,clean):
            if len(clean[:found.start()].split())>12:continue
            if category=='Moisturizer' and found[0] in ('moisturizing','moisturising'):continue
            prefix=clean[:found.start()]
            if re.search(r'\b(?:use|apply|ingredients|contains|unlike|instead|follow|pair|combine|for|with|without|using)\b',prefix):continue
            if category=='Supplements & Health' and re.search(r'\b(?:serum|cream|topical|face|skin)\b',clean):continue
            matches.append((found.start(),-len(found[0]),found.end()))
    if not matches:return None
    _,_,end=min(matches)
    result=classify_product_attributes(clean[:end],brand)
    if result.classification_status not in ('auto_approved','excluded'):return None
    if result.primary_category=='Moisturizer' and result.application_area=='body':
        result.primary_category='Body Care';result.subcategory='body moisturizer'
    return result,clean[:end]


def normalize(value):
    value=unicodedata.normalize('NFKC',value or '').casefold()
    value=''.join(c for c in unicodedata.normalize('NFD',value) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w%+.]',' ',value).split())


def explicit_skincare_anatomy(title):
    return bool(re.search(r'\b(?:face|facial|body|hand|foot|eyes?|lips?|skin|sunscreen|sunblock|photoderm|uvmune|(?:diaper|nappy)(?: rash)? (?:cream|balm|ointment))\b',normalize(title)))


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
    clean_title=normalize(title).rstrip('.')
    clean_title=re.sub(r'(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])',' ',clean_title)
    # A leading retailer brand is identity evidence, not a product function.
    # In particular, the cosmetics brand Essence must not create a toner match.
    clean_brand=re.sub(r'(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])',' ',normalize(brand))
    if clean_brand and clean_title.startswith(clean_brand+' '):
        clean_title=clean_title[len(clean_brand):].strip()
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
        'body' if re.search(r'\b(?:body|hands?|foot|feet|shower)\b',clean_title) else 'face')
    vehicle_title=re.sub(r'\bcreme\b','cream',clean_title)
    vehicle_title=re.sub(r'\bbaume\b','balm',vehicle_title)
    vehicle_title=re.sub(r'\bfluide\b','fluid',vehicle_title)
    vehicle=next((v for v in ('fluid','cream','lotion','serum','balm','oil','gel','foam','mist','stick','patch')
                  if re.search(r'\b'+v+r'\b',vehicle_title)),None)
    concerns=[name for name,pattern in [('UV protection',r'spf|sunscreen'),('acne',r'acne|blemish'),
        ('pigmentation',r'pigment|brighten|dark spots'),('hydration',r'hydrat|moistur'),('barrier repair',r'barrier|cica')]
        if re.search(pattern,clean_title)]
    evidence=[]
    # Explicit product types in the title take precedence over incidental description words.
    excluded=next(((cat,pat) for cat,pat in HARD_EXCLUSIONS if re.search(pat,clean_title)),None)
    # Preserve established hair-brand context without treating an arbitrary
    # brand word (such as Essence) as a skincare function.
    if not excluded and re.search(r'\b(?:kerastase|professionnel|frizz|hairburst|hairoots|hairvogine)\b',normalize(brand)):
        excluded=('Hair Care','known hair-care brand context')
    if excluded and excluded[0]=='Makeup' and re.search(excluded[1],clean_title)[0]=='primer' and re.search(CATEGORY_PATTERNS[0][1],clean_title):
        excluded=None
    if excluded and excluded[0]=='Supplements & Health' and re.search(r'\b(?:serum|cream|topical|face|skin)\b',clean_title):
        excluded=None  # topical capsules are evaluated by product function below
    if excluded and excluded[0]=='Baby Care' and re.search(r'\b(?:diaper|nappy)(?: rash)? (?:cream|balm|ointment)\b',clean_title):
        excluded=None
    if excluded and excluded[0]=='Nail Care' and re.search(r'\bhands? (?:(?:and )?nails? )?(?:cream|balm|serum|lotion)\b',clean_title):
        excluded=None
    bundle=bool(re.search(r'\b(?:gift set|routine set|kit|bundle|set of)\b',clean_title))
    if re.search(r'\bset$',clean_title) and not re.search(r'\bfrom set$',clean_title):bundle=True
    if re.search(r'\bset\s+\d+\b',clean_title):bundle=True
    if normalize(product_type)=='feminine care' and re.search(r'\b(?:pads|wings|panty ?liners)\b',clean_title):
        excluded=('Medical Supplies',r'feminine care pads or liners')
    if bundle: excluded=('Set','bundle')
    if excluded:
        category=excluded[0]
        return Classification('other_beauty' if category in {'Makeup','Fragrance','Hair Care','Deodorant'} else 'non_skincare',
            category,category,area,vehicle,'not_applicable',features,concerns,False,.99,'excluded',evidence=[{'source':'title','match':excluded[1]}])
    if EXPLICIT_PRODUCT_TYPES.get(normalize(product_type))=='Hair Care' and not explicit_skincare_anatomy(title):
        return Classification('other_beauty','Hair Care','hair care','scalp',vehicle,'not_applicable',features,concerns,
            False,.96,'excluded',evidence=[{'source':'exact_retailer_product_type','category':'Hair Care','match':normalize(product_type),'weight':4}])
    scores={}
    for category, pattern in CATEGORY_PATTERNS:
        for source,text,weight in sources:
            match=re.search(pattern,text)
            if match:
                scores[category]=scores.get(category,0)+weight
                evidence.append({'source':source,'category':category,'match':match[0],'weight':weight})
    # Contextual functions resolve legitimate overlaps, independent of regex order.
    title_matches={e['category'] for e in evidence if e['source']=='title'}
    if ('Toner' in title_matches and 'Cleanser' in title_matches
            and re.search(r'\b(?:toner|tonique)\b',clean_title)
            and not re.search(r'\b(?:cleanser|wash|soap|micellar|gel|foam|foaming)\b',clean_title)):
        title_matches.discard('Cleanser')  # "cleansing" can describe a toner's function
    priorities=['Sunscreen','Eye Skincare','Lip Care','Tanning','Body Care','Cleanser','Mask','Toner','Exfoliator','Serum','Treatment','Moisturizer']
    category=next((c for c in priorities if c in title_matches),None)
    explicit_type=EXPLICIT_PRODUCT_TYPES.get(normalize(product_type))
    if category is None and explicit_type:
        category=explicit_type
        evidence.append({'source':'exact_retailer_product_type','category':category,'match':normalize(product_type),'weight':1})
    declaration=None
    if category is None:
        declaration=description_declaration(description,brand)
        if declaration:
            declared,phrase=declaration
            category=declared.primary_category
            evidence.append({'source':'product_description_declaration','category':category,'match':phrase,'weight':3})
            if vehicle is None:vehicle=declared.formulation_type
            if area=='face' and not re.search(r'\b(?:face|facial)\b',clean_title):area=declared.application_area
    if category is None and scores:
        category=max(scores,key=scores.get)
    conflicts=[]
    conflicting_functions=title_matches & {'Cleanser','Sunscreen','Toner'}
    if 'Sunscreen' in conflicting_functions and not re.search(r'\b(?:toner|tonique|thermal (?:spring )?water|eau thermale|facial mist)\b',clean_title):
        conflicting_functions.discard('Toner')  # sun essence is a sunscreen vehicle
    if len(conflicting_functions)>1:
        conflicts.append('Conflicting product functions in title')
    if category is None:
        # Preserve established specialized language handling; uncertainty remains explicit.
        from pipeline.classifier import _legacy_classify_and_tag
        legacy,eligible=_legacy_classify_and_tag(clean_title,'',product_type)
        category=legacy
        if legacy=='Other Non-Skincare':
            return Classification('unresolved',legacy,'unresolved',area,vehicle,'unknown',features,concerns,False,0,'review',evidence=evidence)
        if not eligible and legacy=='Supplements & Health':
            # A nutrient name alone does not make a topical cosmetic an oral
            # supplement. Actual dosage forms are handled by HARD_EXCLUSIONS.
            return Classification('unresolved','Other Non-Skincare','unresolved',area,vehicle,'unknown',features,concerns,False,0,'review',evidence=evidence)
        if not eligible:
            return Classification('other_beauty',legacy,legacy,area,vehicle,'not_applicable',features,concerns,False,.9,'excluded',evidence=evidence)
    eligible=category in SKINCARE
    confidence=.96 if title_matches and not conflicts else (.9 if (explicit_type or declaration) and not conflicts else (.8 if not conflicts else .5))
    status=('auto_approved' if eligible else 'excluded') if (title_matches or explicit_type or declaration) and not conflicts else 'review'
    usage='rinse_off' if category=='Cleanser' or re.search(r'\b(?:body wash|hand wash|shower gel|bath foam|glycerin soap|bath bomb)\b',clean_title) or 'peel' in clean_title else 'leave_on'
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
