"""Audited identity/category decisions, reversible merge/split, and redirects."""
import json
from dataclasses import replace
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver, compare_attributes, extract_identity, identity_name
from pipeline.offer_service import refresh_offer_aggregates
from pipeline.taxonomy_service import SKINCARE, classify_product_attributes, reconcile_canonical_classification


def require_review(reviewer,reason):
    if not reviewer or not reviewer.strip() or not reason or not reason.strip():
        raise ValueError('A named reviewer and a review reason are required.')


def resolve_canonical_redirect(conn,cid):
    seen=set()
    while True:
        if cid in seen:raise ValueError('Canonical redirect cycle detected.')
        seen.add(cid)
        row=conn.execute('SELECT target_id FROM canonical_redirects WHERE canonical_product_id=?',(cid,)).fetchone()
        if not row:return cid
        cid=row[0]


def resolve_review(conn,review_id,action,reviewer,reason,canonical_id=None,category=None):
    require_review(reviewer,reason)
    row=conn.execute('SELECT * FROM product_review_queue WHERE id=?',(review_id,)).fetchone()
    if not row:raise ValueError('Review case not found.')
    case=dict(row);details=json.loads(case['details'])
    if action in ('link','cannot_link'):
        if case['source_id'] in (None,'canonical'):raise ValueError('Use the audited merge or canonical constraint workflow for canonical-only cases.')
        if canonical_id is None:raise ValueError('Canonical ID is required.')
        if not conn.execute('SELECT 1 FROM canonical_products WHERE id=?',(canonical_id,)).fetchone():raise ValueError('Canonical ID not found.')
        if action=='link' and details.get('product_id') and not details.get('observation_id'):
            product=conn.execute('SELECT * FROM products WHERE id=?',(details['product_id'],)).fetchone()
            canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(canonical_id,)).fetchone()
            if not product or product['store_id']!=case['source_id']:raise ValueError('Legacy product not found for this review.')
            attrs=extract_identity(product['original_title'] or product['title'],product['brand'])
            known=json.loads(canonical['identity_attributes'] or '{}') or extract_identity(canonical['canonical_title'],canonical['brand'])
            if compare_attributes(attrs,known)['conflicts']:raise ValueError('Hard identity conflicts must be resolved before accepting this mapping.')
            CanonicalIdentityResolver(conn).add_aliases(canonical_id,attrs,case['source_id'])
        if action=='link' and details.get('observation_id'):
            observation=conn.execute('SELECT identity_attributes FROM source_product_observations WHERE id=?',(details['observation_id'],)).fetchone()
            canonical=conn.execute('SELECT * FROM canonical_products WHERE id=?',(canonical_id,)).fetchone()
            if observation:
                attrs=json.loads(observation[0])
                known=json.loads(canonical['identity_attributes'] or '{}') or extract_identity(canonical['canonical_title'],canonical['brand'])
                if compare_attributes(attrs,known)['conflicts']:raise ValueError('Hard identity conflicts must be resolved before accepting this mapping.')
                CanonicalIdentityResolver(conn).add_aliases(canonical_id,attrs,case['source_id'])
        CanonicalIdentityResolver(conn).record_link(case['source_id'],case['source_product_id'],canonical_id,
            'accepted' if action=='link' else 'rejected','manual_review',details,reviewer)
    elif action=='category':
        if not conn.execute('SELECT 1 FROM product_taxonomy WHERE label=?',(category,)).fetchone():raise ValueError('Unknown taxonomy category.')
        if case['canonical_product_id']:
            canonical=conn.execute('SELECT brand FROM canonical_products WHERE id=?',(case['canonical_product_id'],)).fetchone()
            result=classify_product_attributes(details.get('title') or '',canonical['brand'] if canonical else details.get('brand',''))
            eligible=category in SKINCARE
            area={'Eye Skincare':'eye','Eye Care':'eye','Lip Care':'lip','Body Care':'body'}.get(category,result.application_area)
            vertical='skincare' if eligible else ('other_beauty' if category in {'Makeup','Fragrance','Hair Care','Deodorant'} else 'non_skincare')
            result=replace(result,primary_category=category,is_skincare=eligible,
                vertical=vertical,application_area=area,
                subcategory=f'{area} {category.lower()}' if eligible else category,
                usage_type=result.usage_type if eligible else 'not_applicable',
                classification_status='approved',classification_source='manual',
                evidence=[*result.evidence,{'source':'explicit_review','category':category,'reviewer':reviewer,'reason':reason}])
            reconcile_canonical_classification(conn,case['canonical_product_id'],result,reviewer)
    elif action=='keep_separate' and len(details.get('canonical_ids',[]))==2:
        pair=sorted(details['canonical_ids'])
        conn.execute('''INSERT INTO canonical_identity_constraints(left_id,right_id,relation,reviewed_by,reason) VALUES (?,?,'cannot_link',?,?) ON CONFLICT(left_id,right_id) DO UPDATE SET relation='cannot_link',reviewed_by=excluded.reviewed_by,reason=excluded.reason''',(*pair,reviewer,reason))
    elif action not in ('dismiss','request_evidence','keep_separate'):
        raise ValueError('Unsupported review action.')
    decision={'action':action,'canonical_product_id':canonical_id,'category':category}
    conn.execute('UPDATE product_review_queue SET status=?,decision=?,reviewed_by=?,review_notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
        ('pending' if action=='request_evidence' else 'confirmed',json.dumps(decision),reviewer,reason,review_id))
    return decision


def _snapshot(conn,cids):
    placeholders=','.join('?' for _ in cids)
    state={'canonicals':[dict(r) for r in conn.execute(f'SELECT * FROM canonical_products WHERE id IN ({placeholders})',cids)]}
    for table,key in [('products','canonical_product_id'),('product_offers','canonical_product_id'),
                      ('product_identity_links','canonical_product_id'),('canonical_product_aliases','canonical_product_id'),
                      ('canonical_redirects','canonical_product_id')]:
        state[table]=[dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE {key} IN ({placeholders})',cids)]
    return state


def _history(conn,operation,cid,target,before,reviewer,reason):
    after=_snapshot(conn,[cid,target] if target else [cid])
    cursor=conn.execute('''INSERT INTO canonical_identity_history(operation,canonical_product_id,target_id,before_state,after_state,reviewed_by,reason)
        VALUES (?,?,?,?,?,?,?)''',(operation,cid,target,json.dumps(before),json.dumps(after),reviewer,reason))
    return cursor.lastrowid


def merge_canonicals(conn,survivor_id,merged_id,reviewer,reason):
    require_review(reviewer,reason)
    if survivor_id==merged_id:raise ValueError('Choose two different canonical IDs.')
    records=[dict(r) for r in conn.execute('SELECT * FROM canonical_products WHERE id IN (?,?)',(survivor_id,merged_id))]
    if len(records)!=2:raise ValueError('Canonical record not found.')
    mapping={r['id']:r for r in records}
    for cid in (survivor_id,merged_id):
        if resolve_canonical_redirect(conn,cid)!=cid:raise ValueError('Resolve redirects before merging.')
    left,right=mapping[survivor_id],mapping[merged_id]
    if left.get('brand_id')!=right.get('brand_id') or not left.get('brand_id'):
        raise ValueError('Verified brand identities must agree before merging.')
    attrs=[json.loads(p.get('identity_attributes') or '{}') or extract_identity(p['canonical_title'],p['brand']) for p in (left,right)]
    if compare_attributes(*attrs)['conflicts']:raise ValueError('Resolve hard identity conflicts before merging.')
    pair=sorted([survivor_id,merged_id])
    constraint=conn.execute('SELECT relation FROM canonical_identity_constraints WHERE left_id=? AND right_id=?',pair).fetchone()
    if constraint and constraint[0]=='cannot_link':raise ValueError('A reviewed cannot-link constraint blocks this merge.')
    # A merge never silently chooses between contradictory accepted formulas.
    if not left.get('ingredients_raw') and right.get('ingredients_raw'):
        raise ValueError('Retain the formula-bearing canonical as survivor, or approve its formulation on the survivor before merging.')
    if left.get('ingredients_raw') and right.get('ingredients_raw'):
        from pipeline.identity_review import formula_fingerprint
        if not formula_fingerprint(left) or formula_fingerprint(left)!=formula_fingerprint(right):raise ValueError('Resolve formulation disagreement before merging.')
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT identity_merge')
    try:
        before=_snapshot(conn,[survivor_id,merged_id])
        for table in ('products','product_offers'):
            conn.execute(f'UPDATE {table} SET canonical_product_id=? WHERE canonical_product_id=?',(survivor_id,merged_id))
        for link in conn.execute('SELECT * FROM product_identity_links WHERE canonical_product_id=?',(merged_id,)).fetchall():
            link=dict(link)
            CanonicalIdentityResolver(conn).record_link(link['source_id'],link['source_product_id'],survivor_id,link['status'],
                'reviewed_merge',{'previous_canonical_id':merged_id,'reason':reason},reviewer)
        for alias in conn.execute('SELECT * FROM canonical_product_aliases WHERE canonical_product_id=?',(merged_id,)).fetchall():
            alias=dict(alias)
            conn.execute('INSERT OR IGNORE INTO canonical_product_aliases(canonical_product_id,alias_type,alias_value,source_id,verified) VALUES (?,?,?,?,?)',
                (survivor_id,alias['alias_type'],alias['alias_value'],alias['source_id'],alias['verified']))
        conn.execute("INSERT OR IGNORE INTO canonical_product_aliases(canonical_product_id,alias_type,alias_value,source_id,verified) VALUES (?,'title',?,'reviewed_merge',1)",
            (survivor_id,attrs[1]['name']))
        conn.execute("UPDATE canonical_products SET catalog_visible=0,identity_status='redirected' WHERE id=?",(merged_id,))
        refresh_offer_aggregates(conn,survivor_id);refresh_offer_aggregates(conn,merged_id)
        hid=_history(conn,'merge',merged_id,survivor_id,before,reviewer,reason)
        conn.execute('INSERT INTO canonical_redirects(canonical_product_id,target_id,history_id) VALUES (?,?,?)',(merged_id,survivor_id,hid))
        # Include the redirect in the reversible post-state.
        conn.execute('UPDATE canonical_identity_history SET after_state=? WHERE id=?',
            (json.dumps(_snapshot(conn,[survivor_id,merged_id])),hid))
        conn.execute('RELEASE SAVEPOINT identity_merge')
        return hid
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT identity_merge');conn.execute('RELEASE SAVEPOINT identity_merge');raise


def split_canonical(conn,cid,product_ids,offer_ids,title,attributes,reviewer,reason):
    require_review(reviewer,reason)
    original=conn.execute('SELECT * FROM canonical_products WHERE id=?',(cid,)).fetchone()
    if not original:raise ValueError('Canonical record not found.')
    original=dict(original)
    if not product_ids and not offer_ids:raise ValueError('Select source offers to split.')
    for table,ids in [('products',product_ids),('product_offers',offer_ids)]:
        for item_id in ids:
            row=conn.execute(f'SELECT canonical_product_id FROM {table} WHERE id=?',(item_id,)).fetchone()
            if not row or row[0]!=cid:raise ValueError('Selected offer does not belong to this canonical.')
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT identity_split')
    try:
        before=_snapshot(conn,[cid])
        moved_titles=set()
        for table,ids in [('products',product_ids),('product_offers',offer_ids)]:
            for item_id in ids:
                item=conn.execute(f"SELECT {'store_id' if table=='products' else 'source_id'} source_id,title FROM {table} WHERE id=?",(item_id,)).fetchone()
                moved_titles.add((item['source_id'],identity_name(item['title'],original['brand'])))
        classification=classify_product_attributes(title,original['brand'])
        identity=extract_identity(title,original['brand'],original.get('market'),attributes)
        new_id=CanonicalIdentityResolver(conn).create_provisional(title,original['brand'],classification,identity,False)
        for table,ids in [('products',product_ids),('product_offers',offer_ids)]:
            for item_id in ids:conn.execute(f'UPDATE {table} SET canonical_product_id=? WHERE id=?',(new_id,item_id))
        for product_id in product_ids:
            conn.execute('UPDATE product_offers SET canonical_product_id=? WHERE legacy_product_id=? AND canonical_product_id=?',(new_id,product_id,cid))
        # Source links for moved variant offers are rebound explicitly on review;
        # a product-page mapping cannot silently become a per-variant decision.
        for table,ids in [('products',product_ids),('product_offers',offer_ids)]:
            for item_id in ids:
                source=conn.execute(f"SELECT {'store_id,store_product_id' if table=='products' else 'source_id,source_product_id'} FROM {table} WHERE id=?",(item_id,)).fetchone()
                conn.execute("UPDATE product_identity_links SET status='pending',method='split_requires_review' WHERE source_id=? AND source_product_id=?",(source[0],str(source[1] or item_id)))
        # The old entity must not keep a verified title alias for the exact
        # listing just split out. Keep the row for audit; explicit source-link
        # review can establish an alias on the new identity afterward.
        for source_id,alias in moved_titles:
            conn.execute("""UPDATE canonical_product_aliases SET verified=0
                WHERE canonical_product_id=? AND source_id=? AND alias_type='title' AND alias_value=?""",
                (cid,source_id,alias))
        conn.execute("INSERT INTO canonical_identity_constraints(left_id,right_id,relation,reviewed_by,reason) VALUES (?,?,'cannot_link',?,?)",(*sorted([cid,new_id]),reviewer,reason))
        refresh_offer_aggregates(conn,cid);refresh_offer_aggregates(conn,new_id)
        hid=_history(conn,'split',cid,new_id,before,reviewer,reason)
        conn.execute('RELEASE SAVEPOINT identity_split')
        return {'history_id':hid,'new_canonical_id':new_id}
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT identity_split');conn.execute('RELEASE SAVEPOINT identity_split');raise


def reverse_identity_operation(conn,history_id,reviewer,reason):
    require_review(reviewer,reason)
    row=conn.execute('SELECT * FROM canonical_identity_history WHERE id=?',(history_id,)).fetchone()
    if not row:raise ValueError('History record not found.')
    row=dict(row);before=json.loads(row['before_state']);after=json.loads(row['after_state'])
    cids=[p['id'] for p in after['canonicals']]
    # Refuse to clobber intervening changes, including new formulation publications.
    if _snapshot(conn,cids)!=after:raise ValueError('Identity changed since this operation; review the intervening changes first.')
    if not conn.in_transaction:conn.execute('BEGIN')
    conn.execute('SAVEPOINT identity_reverse')
    try:
        for table in ('products','product_offers'):
            for item in before[table]:
                conn.execute(f'UPDATE {table} SET canonical_product_id=? WHERE id=?',(item['canonical_product_id'],item['id']))
        for canonical in before['canonicals']:
            conn.execute('UPDATE canonical_products SET catalog_visible=?,identity_status=? WHERE id=?',
                (canonical['catalog_visible'],canonical['identity_status'],canonical['id']))
        for table in ('product_identity_links','canonical_product_aliases','canonical_redirects'):
            conn.execute(f"DELETE FROM {table} WHERE canonical_product_id IN ({','.join('?' for _ in cids)})",cids)
            for item in before[table]:
                conn.execute(f"INSERT INTO {table}({','.join(item)}) VALUES ({','.join('?' for _ in item)})",list(item.values()))
        if row['operation']=='split':
            new_id=row['target_id']
            conn.execute("UPDATE canonical_products SET catalog_visible=0,identity_status='reversed_split' WHERE id=?",(new_id,))
            conn.execute('DELETE FROM canonical_identity_constraints WHERE left_id=? AND right_id=?',sorted([row['canonical_product_id'],new_id]))
        for cid in cids:refresh_offer_aggregates(conn,cid)
        hid=_history(conn,'reverse',row['canonical_product_id'],row['target_id'],after,reviewer,reason)
        conn.execute('UPDATE canonical_identity_history SET reverses_id=? WHERE id=?',(history_id,hid))
        conn.execute('RELEASE SAVEPOINT identity_reverse');return hid
    except Exception:
        conn.execute('ROLLBACK TO SAVEPOINT identity_reverse');conn.execute('RELEASE SAVEPOINT identity_reverse');raise


def publish_canonical_identity(conn,canonical_id,reviewer,reason):
    require_review(reviewer,reason)
    row=conn.execute('SELECT * FROM canonical_products WHERE id=?',(canonical_id,)).fetchone()
    if not row:raise ValueError('Canonical record not found.')
    row=dict(row)
    from pipeline.identity_trust import pending_identity_review
    if pending_identity_review(conn,canonical_id):raise ValueError('Resolve pending identity conflicts before publication.')
    if row['identity_status'] in ('redirected','reversed_split'):raise ValueError('This identity is superseded.')
    classification=json.loads(row['classification'] or '{}')
    if classification.get('classification_status') not in ('auto_approved','approved'):
        raise ValueError('Resolve category uncertainty first.')
    if conn.execute("SELECT 1 FROM product_review_queue WHERE canonical_product_id=? AND status='pending'",(canonical_id,)).fetchone():
        raise ValueError('Resolve pending canonical review findings before publication.')
    before=_snapshot(conn,[canonical_id])
    conn.execute("UPDATE canonical_products SET catalog_visible=1,identity_status='verified',classification_status='approved' WHERE id=?",(canonical_id,))
    return _history(conn,'publish',canonical_id,None,before,reviewer,reason)
