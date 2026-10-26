"""HTML-backed ingredient sections, retaining order, partial evidence and formula codes."""
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from pipeline.inci_tokenizer import tokenize_inci_string


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines=[]; self.heading_lines=set(); self.heading_depth=0; self.buffer=[]; self.hidden=0; self.scripts=[]; self.script=None
    def flush(self):
        text=' '.join(''.join(self.buffer).split())
        if text:
            if self.heading_depth:self.heading_lines.add(len(self.lines))
            self.lines.append(text)
        self.buffer=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='script':
            self.hidden+=1
            if attributes.get('type')=='application/ld+json': self.script=[]
        elif tag in ('style','noscript'):self.hidden+=1
        if tag in ('p','div','h1','h2','h3','h4','h5','h6','summary','li','dt','dd','br','button','section'):
            self.flush()
        if tag in ('h1','h2','h3','h4','h5','h6','summary','dt'):self.heading_depth+=1
    def handle_endtag(self,tag):
        if tag=='script':
            if self.script is not None:self.scripts.append(''.join(self.script));self.script=None
            self.hidden=max(0,self.hidden-1)
        elif tag in ('style','noscript'):self.hidden=max(0,self.hidden-1)
        if tag in ('p','div','h1','h2','h3','h4','h5','h6','summary','li','dt','dd','button','section'):
            self.flush()
        if tag in ('h1','h2','h3','h4','h5','h6','summary','dt'):self.heading_depth=max(0,self.heading_depth-1)
    def handle_data(self,data):
        if self.script is not None:self.script.append(data)
        if not self.hidden:self.buffer.append(data)


@dataclass
class IngredientSection:
    text: str
    section_type: str
    order_verified: bool
    completeness: str
    formula_code: str | None = None

FULL = ["what's in it",'what’s in it','ingredients','full ingredients','full ingredients list','list of ingredients','inci','المكونات','قائمة المكونات كاملة','قائمة المكونات الكاملة','جميع المكونات']
PARTIAL = ['key ingredients','key active ingredients','active ingredients','main ingredients','مكونات فعالة','المكونات الرئيسية','المكونات الفعالة']
STOPS = re.compile(r'^(?:(?:brand|size|returns)\s*:|(?:how to use|directions|disclaimer|warnings?|benefits|free from|additional information|reviews?|customer reviews|shipping|delivery information|payment options|description|features|product details|about the brand|please be aware|please refer|for the most|for refilled|ingredients are subject|قد يعجبك|المنتجات التي|احصلي على|طريقة الاستخدام)\b)',re.I)


def extract_sections(html,rules=None,product_title=''):
    parser=DocumentParser();parser.feed(html or '');parser.flush()
    rules=rules or {}
    full=rules.get('full_headers',FULL)
    partial=rules.get('partial_headers',PARTIAL)
    full=list(dict.fromkeys(full+FULL));partial=list(dict.fromkeys(partial+PARTIAL))
    headers=sorted([(h,'partial') for h in partial]+[(h,'full') for h in full],key=lambda p:len(p[0]),reverse=True)
    def header(line):
        for name,kind in headers:
            match=re.match(r'^\s*'+re.escape(name)+r'\s*(?::|[-–]\s|$)\s*(.*)$',line,re.I)
            if match:return kind,match[1]
        return None
    results=[]
    for index,line in enumerate(parser.lines):
        matched=header(line)
        if not matched: continue
        kind,inline=matched
        fragments=[inline] if inline else []
        ignored=('view the full ingredient list','view full ingredients','show ingredients','عرض قائمة المكونات كاملة','عرض المكونات')
        for following_index,following in enumerate(parser.lines[index+1:],start=index+1):
            if following.lower().strip() in ignored:continue
            if following.lower().strip()=='verified common base':
                fragments.append(following);continue
            if header(following) or STOPS.search(following) or following_index in parser.heading_lines:break
            # Navigation ingredient tab labels have no declaration directly after them.
            if len(following)>0:fragments.append(following)
        fragments=[f for f in fragments if f.lower().strip() not in ('view the full ingredient list','view full ingredients','show ingredients')]
        text=', '.join(f.strip(' ,;•') for f in fragments if f.strip(' ,;•'))
        code_match=re.search(r'(?:formula\s*(?:code|number)|f\.i\.l\.?|fil)\s*[:#-]?\s*([A-Z0-9][A-Z0-9 .\-/]{3,35})\s*$',text,re.I)
        code=code_match[1].strip(' ,.') if code_match else None
        if code_match:text=text[:code_match.start()].strip(' ,;')
        text=re.sub(r'\s*[•●]\s*',', ',text)
        validation=tokenize_inci_string(text,product_title=product_title)
        completeness=validation.completeness if kind=='full' else 'partial'
        if not text:continue
        # A navigation header followed by normal prose is not a full declaration.
        if completeness=='full' and (len(validation.tokens)<3 and not validation.validation_flags.get('single_ingredient_formulation')):
            completeness='unverified'
        if any(p in text.lower() for p in ('displayed in alphabetical order','deliberately hidden','ingredient benefits','verified common base','colorants can vary by shade','ingredients vary by shade')):
            completeness='unverified'
        ordered=kind=='full' and completeness=='full' and not validation.is_truncated
        candidate=IngredientSection(text,kind,ordered,completeness,code)
        if not any(r.text==text and r.section_type==kind for r in results):results.append(candidate)
    return results


def preferred_section(sections):
    # Prefer an explicitly labeled full declaration, never whichever list is longest.
    return next((s for s in sections if s.section_type=='full' and s.completeness=='full'),
                next((s for s in sections if s.section_type=='full'),next(iter(sections),None)))


def structured_products(html):
    parser=DocumentParser();parser.feed(html or '')
    def walk(value):
        if isinstance(value,list):
            for item in value:yield from walk(item)
        elif isinstance(value,dict):
            kind=value.get('@type',[])
            if kind=='Product' or isinstance(kind,list) and 'Product' in kind:yield value
            for key in ('@graph','mainEntity','itemListElement','item'):
                if key in value:yield from walk(value[key])
    products=[]
    for script in parser.scripts:
        try:products.extend(walk(json.loads(script)))
        except (ValueError,TypeError):continue
    return products
