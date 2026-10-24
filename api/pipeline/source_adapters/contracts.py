from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import json
from decimal import Decimal, InvalidOperation


def now(): return datetime.now(timezone.utc).isoformat()


def money(value):
    try:
        number=Decimal(str(value or 0))
        if not number.is_finite() or number<0: raise ValueError('Price must be finite and non-negative.')
        return float(number)
    except InvalidOperation as exc:
        raise ValueError('Invalid price') from exc


@dataclass
class SourceVariant:
    source_variant_id: str
    title: str = ''
    sku: str | None = None
    gtin: str | None = None
    price: float = 0
    compare_at_price: float = 0
    available: bool = False
    currency: str = 'EGP'
    attributes: dict = field(default_factory=dict)


@dataclass
class SourceProductObservation:
    source_id: str
    source_type: str
    source_product_id: str
    source_url: str
    raw_title: str
    raw_brand: str
    raw_category: str | None = None
    source_gtin: str | None = None
    manufacturer_sku: str | None = None
    description: str | None = None
    variants: list[SourceVariant] = field(default_factory=list)
    images: list = field(default_factory=list)
    ingredient_text: str | None = None
    ingredient_section_type: str | None = None
    formula_code: str | None = None
    ingredient_order_verified: bool = False
    market: str | None = None
    language: str = 'en'
    observed_at: str = field(default_factory=now)
    raw_payload: dict = field(default_factory=dict)
    identity_attributes: dict = field(default_factory=dict)
    @property
    def raw_payload_hash(self):
        values=asdict(self)
        values.pop('observed_at',None)
        return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
