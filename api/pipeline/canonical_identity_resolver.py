"""Incremental identity resolution against stable canonical profiles, never transitive clusters."""
from dataclasses import dataclass, field, asdict
import hashlib
import json
import re
from difflib import SequenceMatcher
from pipeline.brand_registry import resolve_brand, register_observed_brand, brand_key,CURATED_BRANDS
from pipeline.taxonomy_service import normalize, classify_product_attributes
from pipeline.identity_gates import validate_identity_gates

PACKAGING = re.compile(r'\b(?:\d+\s*[x×]\s*)?\d+(?:\.\d+)?\s*(?:ml|gm?|kg|l|fl\.?\s*oz|oz)\b',re.I)
TRANSLATIONS = {'كريم':'cream','مرطب':'moisturizing','غسول':'cleanser','سيروم':'serum','تونر':'toner',
                'واقي':'sunscreen','الشمس':'','ليل':'night','نهار':'day','moisturising':'moisturizing','creme':'cream','baume':'balm'}


def valid_gtin(value):
    value=re.sub(r'\s','',str(value or ''))
    if not value.isdigit() or len(value) not in (8,12,13,14): return None
    digits=list(map(int,value))
    checksum=sum(d*(3 if i%2==0 else 1) for i,d in enumerate(reversed(digits[:-1])))
    return value.zfill(14) if (10-checksum%10)%10==digits[-1] else None


def identity_name(title, brand):
    text=normalize(PACKAGING.sub(' ',title or ''))
    # Typography is not a formulation discriminator. Keep meaningful '+' in
    # product-line names and PA ratings; SPF50 and SPF 50+ share an SPF value.
    text=re.sub(r'\bspf\s*(\d+)\s*\+?',r'spf \1 ',text)
    resolved=resolve_brand(brand)
    variants=[brand or '']+([resolved['name']] if resolved else [])
    if resolved:
        variants+=CURATED_BRANDS.get(resolved['name'],[])
    for value in variants:
        normalized=normalize(value)
        if normalized:
            # Retailers write "Dr.Althea", "Dr. Althea" and "Dr Althea".
            # Match complete brand tokens, never a prefix of another brand.
            parts=re.findall(r'\w+',normalized)
            pattern=r'[\W_]*'.join(re.escape(p) for p in parts)
            if pattern:text=re.sub(r'(?<!\w)'+pattern+r'(?!\w)',' ',text)
    text=re.sub(r'\b(?:for )?(?:normal|dry|very dry|oily|combination|sensitive)(?: to (?:normal|dry|very dry|oily|combination|sensitive))? skin\b',' ',text)
    words=[TRANSLATIONS.get(w,w) for w in text.split()]
    # Keep formulation-defining rich/light/pure/intensive adjectives. Only this
    # reviewed manufacturer naming equivalence drops 'daily'; it is not a
    # universal marketing stopword or a day/night discriminator.
    words=[w.strip('.') for w in words]
    if resolved and resolved['name']=='CeraVe' and set(words)=={'daily','moisturizing','lotion'}:
        words=[w for w in words if w!='daily']
    return ' '.join(sorted(w for w in words if w and w not in {'new','packaging','with','for','skin','facial','and','the'}))


def extract_identity(title, brand, market=None, attributes=None):
    attributes=dict(attributes or {})
    t=normalize(title)
    classification=classify_product_attributes(title,brand)
    def first(pattern):
        result=re.search(pattern,t)
        return result[1] if result else None
    derived={
        'name':identity_name(title,brand), 'vehicle':classification.formulation_type,
        'application_area':classification.application_area,
        'function':classification.primary_category if classification.classification_status!='review' else None,
        'spf':first(r'\bspf\s*(\d+)\b'),
        'concentrations':sorted(set(re.findall(r'\b\d+(?:\.\d+)?\s*%',t))),
        'timing': 'night' if re.search(r'\b(?:night|pm|nuit)\b',t) else ('day' if re.search(r'\b(?:day|am|jour)\b',t) else None),
        'fragrance':'free' if re.search(r'\b(?:fragrance free|unscented|sans parfum)\b',t) else ('scented' if re.search(r'\b(?:scented|fragranced|perfumed)\b',t) else None),
        'tint':'untinted' if re.search(r'\b(?:untinted|invisible)\b',t) else ('tinted' if re.search(r'\btinted\b',t) else None),
        'market':market, 'region_defining':False, 'formula_revision':None, 'shade':None,
    }
    derived.update({k:v for k,v in attributes.items() if v is not None})
    return derived


def compare_attributes(a,b):
    conflicts=[]; unknown=[]
    for key in ('spf','concentrations','timing','fragrance','tint','shade','formula_revision'):
        x,y=a.get(key),b.get(key)
        if x and y and x!=y: conflicts.append(key)
        elif bool(x)!=bool(y): unknown.append(key)
    for key in ('vehicle','application_area','function'):
        x,y=a.get(key),b.get(key)
        if x and y and x!=y:
            # Face is a default rather than evidence of an explicit anatomy conflict.
            if key=='application_area' and 'face' in (x,y): unknown.append(key)
            else: conflicts.append(key)
        elif bool(x)!=bool(y): unknown.append(key)
    if a.get('region_defining') or b.get('region_defining'):
        if a.get('market') and b.get('market') and a['market']!=b['market']: conflicts.append('market')
        elif not a.get('market') or not b.get('market'): unknown.append('market')
    return {'conflicts':conflicts,'unknown':unknown,'status':'conflict' if conflicts else ('unknown' if unknown else 'compatible')}


@dataclass
class IdentityDecision:
    status: str
    canonical_product_id: int | None = None
    method: str = ''
    candidates: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    def to_dict(self): return asdict(self)


class CanonicalIdentityResolver:
    def __init__(self,conn): self.conn=conn

    def resolve(self, identity, brand, title, source_id=None, source_product_id=None, gtin=None, manufacturer_sku=None):
        resolved=resolve_brand(brand,self.conn)
        # A real new brand is not a product-identity conflict. Preserve the
        # exact vendor label as *unverified*; never fabricate alias equivalence.
        if resolved and not resolved.get('id') and source_id and source_product_id:
            resolved=register_observed_brand(self.conn,brand)
        if not resolved or not resolved.get('id'):
            return IdentityDecision('ambiguous',method='missing_or_unusable_brand',
                                    details={'raw_brand':brand})
        mappings=[]
        if source_id and source_product_id:
            mappings=[dict(r) for r in self.conn.execute('SELECT * FROM product_identity_links WHERE source_id=? AND source_product_id=?',(source_id,source_product_id))]
        rejected={r['canonical_product_id'] for r in mappings if r['status']=='rejected'}
        existing=[r['canonical_product_id'] for r in mappings if r['status']=='accepted']
        # Candidate blocking uses brand identity, never a retailer category.
        rows=[dict(r) for r in self.conn.execute('SELECT * FROM canonical_products c WHERE (brand_id=? OR lower(brand)=lower(?)) AND NOT EXISTS (SELECT 1 FROM canonical_redirects r WHERE r.canonical_product_id=c.id) ORDER BY id',
                                                (resolved['id'],resolved['name']))]
        barcode=valid_gtin(gtin)
        if barcode:
            barcode_rows=[dict(r) for r in self.conn.execute("""SELECT DISTINCT c.* FROM canonical_products c
                JOIN canonical_product_aliases a ON a.canonical_product_id=c.id WHERE a.alias_type='gtin' AND a.alias_value=? AND a.verified=1""",(barcode,))]
            for known in barcode_rows:
                known_brand=resolve_brand(known['brand'],self.conn)
                known_attributes=json.loads(known.get('identity_attributes') or '{}')
                if not known_attributes.get('name'):known_attributes=extract_identity(known['canonical_title'],known['brand'],known.get('market'))
                known_attributes['name']=identity_name(known['canonical_title'],known['brand'])
                comparison=compare_attributes(identity,known_attributes)
                if not known_brand or known_brand['id']!=resolved['id'] or comparison['conflicts'] or identity['name']!=known_attributes.get('name'):
                    verified_alias=self.conn.execute("SELECT 1 FROM canonical_product_aliases WHERE canonical_product_id=? AND alias_type='title' AND alias_value=? AND verified=1",(known['id'],identity['name'])).fetchone()
                    if not verified_alias or comparison['conflicts'] or not known_brand or known_brand['id']!=resolved['id']:
                        return IdentityDecision('ambiguous',method='gtin_identity_conflict',details={'gtin':barcode,'canonical_product_id':known['id'],'comparison':comparison})
        evaluated=[]
        for row in rows:
            cid=row['id']
            if cid in rejected: continue
            blocked=False
            for anchor in existing:
                if anchor==cid: continue
                pair=sorted([anchor,cid])
                relation=self.conn.execute('SELECT relation FROM canonical_identity_constraints WHERE left_id=? AND right_id=?',pair).fetchone()
                if relation and relation[0]=='cannot_link': blocked=True;break
            if blocked: continue
            attributes=json.loads(row.get('identity_attributes') or '{}')
            if not attributes.get('name'):
                attributes=extract_identity(row['canonical_title'],row['brand'],row.get('market'),attributes)
            # Stored attributes can predate typography normalization. Compare
            # both current names under the same policy without rewriting evidence.
            attributes['name']=identity_name(row['canonical_title'],row['brand'])
            comparison=compare_attributes(identity,attributes)
            compatible,reason=validate_identity_gates(title,row['canonical_title'],resolved['name'],row['brand'])
            # The legacy gate rejects missing eye/lip/SPF attributes. Missing is
            # uncertainty here; explicit contradictions are handled above.
            if not compatible and not comparison['conflicts']:
                if reason and any(word in reason for word in ('Line conflict','Vehicle conflict','Circadian','Fragrance','Tint')):
                    comparison['conflicts'].append(reason)
            aliases=[dict(r) for r in self.conn.execute('SELECT * FROM canonical_product_aliases WHERE canonical_product_id=? AND verified=1',(cid,))]
            unverified_origin = self.conn.execute("SELECT 1 FROM product_identity_links WHERE canonical_product_id=? AND identity_status='legacy_preserved_unverified' AND method='legacy_preserved'", (cid,)).fetchone()
            if unverified_origin:
                aliases=[a for a in aliases if a['alias_type']!='title' or not self.conn.execute("SELECT 1 FROM product_identity_links WHERE canonical_product_id=? AND source_id=? AND identity_status='legacy_preserved_unverified'",(cid,a['source_id'])).fetchone()]
            verified_name=any(a['alias_type']=='title' and a['alias_value']==identity['name'] for a in aliases)
            strong=any((a['alias_type']=='gtin' and barcode and a['alias_value']==barcode) or
                (a['alias_type']=='manufacturer_sku' and manufacturer_sku and a['source_id']==source_id and a['alias_value']==manufacturer_sku) or
                (a['alias_type']=='title' and a['alias_value']==identity['name']) for a in aliases)
            exact=identity['name']==attributes.get('name') and bool(identity['name'])
            similarity=SequenceMatcher(None,identity['name'],attributes.get('name','')).ratio()
            words=set(identity['name'].split()); other=set(attributes.get('name','').split())
            overlap=len(words & other)/max(1,len(words | other))
            relevant=cid in existing or strong or exact or overlap>=.35 or similarity>=.72
            if not relevant: continue
            state='conflict' if comparison['conflicts'] else ('unknown' if comparison['unknown'] else 'compatible')
            evaluated.append({'canonical_product_id':cid,'state':state,'conflicts':comparison['conflicts'],
                'unknown':comparison['unknown'],'exact_name':exact,'verified_name':verified_name,'strong_identifier':bool(strong),
                'durable_link':cid in existing,'reviewed_link':any(r['canonical_product_id']==cid and r.get('reviewed_by') and r['status']=='accepted' for r in mappings),'similarity':round(max(similarity,overlap),4)})
        reviewed=[r for r in evaluated if r['reviewed_link'] and r['state']!='conflict']
        if len(reviewed)==1:
            return IdentityDecision('matched',reviewed[0]['canonical_product_id'],'reviewed_source_identity',evaluated)
        accepted=[r for r in evaluated if (r['state']=='compatible' or r['state']=='unknown' and r['reviewed_link']) and (r['exact_name'] or r['verified_name'])]
        if len(accepted)==1:
            candidate=accepted[0]
            # A second equally plausible identity is not resolved by retrieval order.
            rivals=[r for r in evaluated if r['canonical_product_id']!=candidate['canonical_product_id'] and r['state']!='conflict'
                    and (r['exact_name'] or r['strong_identifier'] or r['durable_link'])]
            if not rivals:
                return IdentityDecision('matched',candidate['canonical_product_id'],'durable_link' if candidate['durable_link'] else
                    ('validated_identifier' if candidate['strong_identifier'] else 'exact_structured_identity'),evaluated)
        if evaluated:
            # A fuzzy candidate is not a product comparison. In particular,
            # a weak lexical suggestion with missing variant evidence and no
            # durable listing/verified identifier must not generate a Gemini
            # job or strand a genuine new product in mandatory human review.
            # Keep suggestions only as audit metadata, never as pending links.
            soft=[r for r in evaluated if
                r['state'] in ('compatible','unknown')
                and not r['conflicts']
                # Missing SPF, tint, concentration, market or formula version
                # may describe the SAME marketed product. Do not manufacture
                # a second canonical just because that discriminator is absent.
                and not set(r.get('unknown') or []) & {
                    'spf','concentrations','timing','fragrance','tint',
                    'shade','formula_revision','market'}
                and not any(r[key] for key in
                    ('durable_link','exact_name','verified_name',
                     'strong_identifier','reviewed_link'))
                and r['similarity'] < 0.85]
            if len(soft)==len(evaluated) and not existing:
                return IdentityDecision('new',method='no_defensible_existing_match',
                    details={'ignored_weak_candidates':[r['canonical_product_id'] for r in soft],
                             'rule':'No exact marketed name, strong identifier, or durable accepted identity; fuzzy similarity is not a comparison.'})
            # In restored historical data, an unverified canonical with no
            # retailer listing, accepted source link or authoritative formula
            # is only a *name suggestion*. Missing SPF/vehicle/formulation
            # evidence from such an ungrounded record cannot create a human
            # comparison obligation for an unrelated saved Shopify product.
            # Keep durable links, exact names, strong identifiers, verified
            # canonicals, and near-exact marketed titles protected.
            possible_unbacked=[r for r in evaluated if (
                not r['conflicts']
                and not any(r[key] for key in ('durable_link','exact_name',
                          'verified_name','strong_identifier','reviewed_link'))
                and r['similarity'] < .85)]
            if not existing and len(possible_unbacked)==len(evaluated):
                def lacks_live_comparator(candidate_id):
                    record=self.conn.execute("""SELECT identity_status,ingredients_raw
                        FROM canonical_products WHERE id=?""",(candidate_id,)).fetchone()
                    if not record or record['identity_status']=='verified' or (
                        record['ingredients_raw'] and str(record['ingredients_raw']).strip()):
                        return False
                    if self.conn.execute("""SELECT 1 FROM product_offers
                        WHERE canonical_product_id=? LIMIT 1""",(candidate_id,)).fetchone():
                        return False
                    if self.conn.execute("""SELECT 1 FROM products
                        WHERE canonical_product_id=? LIMIT 1""",(candidate_id,)).fetchone():
                        return False
                    if self.conn.execute("""SELECT 1 FROM product_identity_links
                        WHERE canonical_product_id=? AND status='accepted'
                        LIMIT 1""",(candidate_id,)).fetchone():
                        return False
                    return True
                if all(lacks_live_comparator(r['canonical_product_id'])
                       for r in possible_unbacked):
                    return IdentityDecision('new',method='unbacked_fuzzy_candidates',
                        details={'ignored_candidate_ids':[
                            r['canonical_product_id'] for r in possible_unbacked],
                            'rule':'Unverified canonicals without retailer listings, accepted links or INCI are not grounded comparisons.'})
            # Critical, fully specified conflicts are separate marketed variants;
            # missing discriminator values cannot justify creating a duplicate.
            if all(r['state']=='conflict' for r in evaluated) and not existing:
                return IdentityDecision('new',method='distinct_variant',candidates=evaluated)
            return IdentityDecision('ambiguous',method='review_required',candidates=evaluated)
        return IdentityDecision('new',method='new_identity')

    def create_provisional(self,title,brand,classification,identity,visible=False):
        resolved=resolve_brand(brand,self.conn)
        if resolved and not resolved.get('id'):
            resolved=register_observed_brand(self.conn,brand)
        if not resolved or not resolved.get('id'):
            raise ValueError('A usable source vendor or brand is required to create a canonical identity.')
        import uuid
        result=self.conn.execute('''INSERT INTO canonical_products(canonical_title,brand,category,normalized_key,brand_id,
            identity_attributes,identity_status,classification,classification_status,catalog_visible,is_skincare,market)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(title,resolved['name'],classification.primary_category,'identity:'+uuid.uuid4().hex,
            resolved['id'],json.dumps(identity),'verified' if visible and resolved['verified'] else 'provisional',json.dumps(classification.to_dict()),
            classification.classification_status,int(visible),int(classification.is_skincare),identity.get('market')))
        return result.lastrowid

    def record_link(self,source_id,source_product_id,canonical_id,status,method,evidence,reviewer=None):
        trust = 'identity_verified' if status == 'accepted' and (reviewer or method not in ('legacy_preserved','requires_review')) else 'legacy_preserved_unverified'
        self.conn.execute('''INSERT INTO product_identity_links(source_id,source_product_id,canonical_product_id,status,method,evidence,reviewed_by,identity_status)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_id,source_product_id,canonical_product_id) DO UPDATE SET
            status=excluded.status,identity_status=excluded.identity_status,method=excluded.method,evidence=excluded.evidence,reviewed_by=COALESCE(excluded.reviewed_by,reviewed_by),updated_at=CURRENT_TIMESTAMP''',
            (source_id,source_product_id,canonical_id,status,method,json.dumps(evidence),reviewer,trust))

    def add_aliases(self,canonical_id,identity,source_id,gtin=None,manufacturer_sku=None):
        aliases=[('title',identity['name'])]
        if valid_gtin(gtin): aliases.append(('gtin',valid_gtin(gtin)))
        if manufacturer_sku: aliases.append(('manufacturer_sku',manufacturer_sku))
        for kind,value in aliases:
            self.conn.execute('INSERT OR IGNORE INTO canonical_product_aliases(canonical_product_id,alias_type,alias_value,source_id,verified) VALUES (?,?,?,?,1)',
                              (canonical_id,kind,value,source_id))
