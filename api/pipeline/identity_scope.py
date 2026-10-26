"""Stable source identity scope shared by ingestion and offer projections."""
import hashlib
import json


def identity_link_product_id(source_product_id,attributes):
    if not isinstance(attributes,dict):raise ValueError('Variant identity attributes must be an object.')
    return str(source_product_id)+(':'+hashlib.sha256(json.dumps(attributes,sort_keys=True).encode()).hexdigest()[:16] if attributes else '')


def backfill_offer_identity_scopes(conn):
    """Derive scope only from the offer's exact retained observation/variant."""
    counts={'backfilled':0,'scoped':0,'invalid_metadata':0}
    rows=conn.execute('''SELECT o.id,o.source_product_id,v.attributes,obs.source_id,obs.source_product_id,o.source_id FROM product_offers o
        LEFT JOIN source_product_observations obs ON obs.id=o.observation_id
        LEFT JOIN source_product_variants v ON v.observation_id=o.observation_id
        AND v.source_variant_id=o.source_variant_id WHERE o.identity_link_product_id IS NULL''').fetchall()
    for row in rows:
        try:
            if row[3] is not None and (row[3]!=row[5] or str(row[4])!=str(row[1])):
                raise ValueError('Offer observation belongs to a different source product.')
            payload=json.loads(row[2]) if row[2] is not None else {}
            if not isinstance(payload,dict):raise ValueError('Invalid retained variant.')
            attrs=payload.get('attributes')
            if attrs is None:attrs={}
            key=identity_link_product_id(row[1],attrs)
            counts['scoped']+=bool(attrs)
        except (ValueError,TypeError,UnicodeError):
            key='';counts['invalid_metadata']+=1
        conn.execute('UPDATE product_offers SET identity_link_product_id=? WHERE id=?',(key,row[0]))
        counts['backfilled']+=1
    return counts
