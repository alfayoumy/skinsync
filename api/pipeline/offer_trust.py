"""Shared source-association proof for public offer projections."""

# A legacy canonical pointer is not proof that two retailer listings match.
VERIFIED_LEGACY_OFFER = """EXISTS(SELECT 1 FROM product_identity_links l
    WHERE l.source_id=p.store_id
      AND l.source_product_id=COALESCE(NULLIF(p.store_product_id,''),CAST(p.id AS TEXT))
      AND l.canonical_product_id=p.canonical_product_id
      AND l.status='accepted' AND l.identity_status='identity_verified')"""
VERIFIED_VARIANT_OFFER = """EXISTS(SELECT 1 FROM product_identity_links l
    WHERE l.source_id=o.source_id AND l.source_product_id=COALESCE(o.identity_link_product_id,o.source_product_id)
    AND (COALESCE(o.identity_link_product_id,o.source_product_id)=o.source_product_id
         OR substr(o.identity_link_product_id,1,length(o.source_product_id)+1)=o.source_product_id || ':')
      AND l.canonical_product_id=o.canonical_product_id
      AND l.status='accepted' AND l.identity_status='identity_verified')"""


VERIFIED_OFFERS_CTE = f"""eligible_variants AS (
    SELECT o.* FROM product_offers o JOIN sources s ON s.id=o.source_id
    WHERE s.provides_prices=1 AND (s.wave<=1 OR s.publish_enabled=1)
      AND o.currency='EGP' AND {VERIFIED_VARIANT_OFFER}
), verified_offers AS (
    SELECT canonical_product_id,source_id,price,is_available,product_url,image_url,
        'variant:' || id AS offer_key FROM eligible_variants
    UNION ALL
    SELECT p.canonical_product_id,p.store_id,p.price,p.is_available,p.product_url,p.image_url,
        'legacy:' || p.id AS offer_key FROM products p
    WHERE {VERIFIED_LEGACY_OFFER} AND NOT EXISTS(
        SELECT 1 FROM eligible_variants v WHERE v.legacy_product_id=p.id)
)"""
