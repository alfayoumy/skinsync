"""Adapters normalize evidence; they never create canonicals or publish INCI."""
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from pipeline.source_adapters.contracts import SourceProductObservation, SourceVariant, money
from pipeline.source_adapters.extraction import extract_sections, preferred_section, structured_products


def variant_attributes(options,variant):
    result={}
    for index,option in enumerate(options):
        name=option.get('name','') if isinstance(option,dict) else str(option)
        value=variant.get('option'+str(index+1))
        if not value:continue
        key=name.lower()
        if key in ('shade','colour','color'):result['shade']=value
        elif key in ('fragrance','scent'):result['fragrance']=value
        elif key=='spf':result['spf']=re.sub(r'\D','',str(value))
        elif key in ('region','market'):result['market']=value;result['region_defining']=True
        elif key in ('formula','formula version'):result['formula_revision']=value
    return result


class ShopifyAdapter:
    def __init__(self,source): self.source=source
    def parse(self,payload,url=None):
        if isinstance(payload,str):payload=json.loads(payload)
        products=payload.get('products',[payload.get('product',payload)])
        observations=[]
        for product in products:
            title=str(product.get('title') or '').strip()
            pid=str(product.get('id') or product.get('handle') or '')
            if not title or not pid:raise ValueError('Shopify product requires a stable ID and title.')
            html=product.get('body_html') or ''
            section=preferred_section(extract_sections(html,self.source.get('section_rules'),title))
            variants=[]
            for v in product.get('variants',[]):
                vid=str(v.get('id') or v.get('sku') or ('default' if len(product.get('variants',[]))==1 else ''))
                if not vid:raise ValueError('Variant requires a stable source ID or SKU.')
                variants.append(SourceVariant(vid,str(v.get('title') or ''),v.get('sku'),v.get('barcode'),money(v.get('price')),
                    money(v.get('compare_at_price')),bool(v.get('available')),str(v.get('currency') or 'EGP'),
                    variant_attributes(product.get('options',[]),v)))
            observations.append(SourceProductObservation(self.source['id'],self.source['source_type'],pid,
                url or f"https://{self.source['domain']}/products/{product.get('handle',pid)}",title,str(product.get('vendor') or ''),
                product.get('product_type'),description=html,variants=variants,
                images=[i['src'] for i in product.get('images',[]) if i.get('src')],
                ingredient_text=section.text if section else None,ingredient_section_type=section.section_type if section else None,
                formula_code=section.formula_code if section else None,ingredient_order_verified=section.order_verified if section else False,
                market=self.source['market'],language=self.source['language'],raw_payload=product))
        return observations


class WooCommerceAdapter:
    def __init__(self,source):self.source=source
    def parse(self,payload,url=None):
        if isinstance(payload,str):payload=json.loads(payload)
        items=payload if isinstance(payload,list) else [payload]
        result=[]
        for p in items:
            title=p.get('name','');html=p.get('description') or ''
            section=preferred_section(extract_sections(html,self.source.get('section_rules'),title))
            raw_variants=p.get('variation_records') or ([p] if p.get('type')!='variable' else [])
            variants=[]
            for v in raw_variants:
                attrs={a.get('name','').lower():a.get('option') for a in v.get('attributes',[]) if a.get('option')}
                variants.append(SourceVariant(str(v['id']),' / '.join(str(x) for x in attrs.values()),v.get('sku'),v.get('global_unique_id'),
                    money(v.get('price')),money(v.get('regular_price')),v.get('stock_status')=='instock',
                    v.get('currency','EGP'),{k:attrs[k] for k in ('shade','fragrance','spf') if k in attrs}))
            brands=p.get('brands') or []
            result.append(SourceProductObservation(self.source['id'],self.source['source_type'],str(p['id']),url or p.get('permalink',''),title,
                brands[0].get('name','') if brands else p.get('brand',''),
                ' > '.join(c.get('name','') for c in p.get('categories',[])),description=html,variants=variants,
                images=[i['src'] for i in p.get('images',[]) if i.get('src')],ingredient_text=section.text if section else None,
                ingredient_section_type=section.section_type if section else None,formula_code=section.formula_code if section else None,
                ingredient_order_verified=section.order_verified if section else False,market=self.source['market'],language=self.source['language'],raw_payload=p))
        return result


class MetaParser(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.meta={};self.title=[];self.h1=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta':self.meta[a.get('property') or a.get('name') or '']=a.get('content','')
        if tag=='h1':self.h1=True
    def handle_endtag(self,tag):
        if tag=='h1':self.h1=False
    def handle_data(self,text):
        if self.h1:self.title.append(text)


class HTMLAdapter:
    def __init__(self,source):self.source=source
    def parse(self,payload,url=None):
        if not url:raise ValueError('A product-page URL is required.')
        html=payload.decode() if isinstance(payload,bytes) else str(payload)
        products=structured_products(html)
        meta=MetaParser();meta.feed(html)
        unique={}
        for product in products:
            unique[(product.get('name'),product.get('sku') or product.get('url') or product.get('productID'))]=product
        products=list(unique.values())
        if len(products)>1:
            requested=urlparse(url).path.rstrip('/')
            exact=[p for p in products if urlparse(urljoin(url,p.get('url') or p.get('@id') or '')).path.rstrip('/')==requested
                   and (p.get('url') or p.get('@id'))]
            if not exact:
                heading=' '.join(meta.title).strip().casefold()
                exact=[p for p in products if str(p.get('name') or '').strip().casefold()==heading]
            if exact:products=exact
        if len(products)>1:
            # Collection pages cannot assign one shared ingredient block to many products.
            raise ValueError('Product-detail extraction requires exactly one product identity.')
        meta=MetaParser();meta.feed(html)
        p=products[0] if products else {}
        title=p.get('name') or meta.meta.get('og:title') or ' '.join(meta.title).strip()
        if not title:raise ValueError('Product identity could not be extracted.')
        brand=p.get('brand') or meta.meta.get('product:brand') or self.source.get('section_rules',{}).get('official_brand') or ''
        if isinstance(brand,dict):brand=brand.get('name','')
        section=preferred_section(extract_sections(html,self.source.get('section_rules'),title))
        offers=p.get('offers') or []
        if isinstance(offers,dict):offers=[offers]
        variants=[]
        for index,o in enumerate(offers):
            if o.get('@type')=='AggregateOffer' or o.get('price') is None:continue
            variants.append(SourceVariant(str(o.get('sku') or o.get('@id') or p.get('sku') or index),
                str(o.get('name') or ''),o.get('sku') or p.get('sku'),o.get('gtin13') or p.get('gtin13'),money(o['price']),
                0,'instock' in str(o.get('availability','')).lower(),o.get('priceCurrency','EGP')))
        images=p.get('image') or []
        if isinstance(images,str):images=[images]
        if isinstance(images,dict):images=[images.get('url')]
        return [SourceProductObservation(self.source['id'],self.source['source_type'],str(p.get('productID') or p.get('sku') or url),url,title,str(brand),
            p.get('category'),p.get('gtin13') or p.get('gtin12') or p.get('gtin8'),p.get('mpn'),p.get('description') or html,
            variants,images,section.text if section else None,section.section_type if section else None,
            section.formula_code if section else None,section.order_verified if section else False,
            self.source['market'],self.source['language'],raw_payload={'html':html})]


class OpenBeautyFactsAdapter:
    def __init__(self,source):self.source=source
    def parse(self,payload,url=None):
        if isinstance(payload,str):payload=json.loads(payload)
        products=payload.get('products',[payload.get('product',payload)])
        return [SourceProductObservation(self.source['id'],'reference',str(p.get('code') or p.get('_id')),
            url or f"https://{self.source['domain']}/product/{p.get('code') or p.get('_id')}",
            p.get('product_name') or '',p.get('brands') or '',p.get('categories'),str(p.get('code') or ''),
            ingredient_text=p.get('ingredients_text'),ingredient_section_type='full' if p.get('ingredients_text') else None,
            ingredient_order_verified=bool(p.get('ingredients_text')),market=self.source['market'],raw_payload=p) for p in products]


def get_adapter(source):
    classes={'shopify':ShopifyAdapter,'woocommerce':WooCommerceAdapter,'html':HTMLAdapter,
             'structured_data':HTMLAdapter,'openbeautyfacts':OpenBeautyFactsAdapter}
    if source['adapter_type'] not in classes:raise ValueError('Unsupported source adapter: '+source['adapter_type'])
    return classes[source['adapter_type']](source)
