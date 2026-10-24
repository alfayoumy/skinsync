import json
import sqlite3
import unittest
from unittest.mock import patch
from pipeline.db_manager import init_db
from pipeline.canonical_identity_resolver import CanonicalIdentityResolver,extract_identity
from pipeline.taxonomy_service import classify_product_attributes
from pipeline.aggregation_service import queue_review
from pipeline.product_identity_gemini import queue_cases
from pipeline.saved_gemini_decisions import apply_saved_decisions
from pipeline.brand_registry import approve_brand_alias


class SavedGeminiDecisionTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        init_db(conn=self.conn,seed_defaults=False)

    def make_case(self,name,brand,listings,verdict,confidence):
        approve_brand_alias(self.conn,brand,brand,'test')
        resolver=CanonicalIdentityResolver(self.conn)
        cid=resolver.create_provisional(name,brand,classify_product_attributes(name,brand),
            extract_identity(name,brand),visible=False)
        self.conn.execute("UPDATE canonical_products SET identity_status='legacy' WHERE id=?",(cid,))
        details={'title':name,'brand':brand,'canonical_ids':[cid],'offers':[]}
        for i,(shop,title) in enumerate(listings):
            pid=10000+cid*100+i
            self.conn.execute("""INSERT INTO products(id,store_id,store_product_id,title,brand,category,product_url,canonical_product_id)
                VALUES (?,?,?,?,?,'Treatment',?,?)""",
                (pid,shop,str(pid),title,brand,'https://'+shop+'.example/'+str(pid),cid))
            details['offers'].append({'store_id':shop,'product_id':pid,'title':title,'brand':brand})
        rid=queue_review(self.conn,'identity',listings[0][0],str(details['offers'][0]['product_id']),cid,details)
        queue_cases(self.conn,review_ids=[rid],queued_by='test')
        self.conn.execute("""UPDATE identity_gemini_queue
            SET status='needs_manual',verdict=?,confidence=?,explanation=?,
                model_name='test-model' WHERE review_id=?""",
            (verdict,confidence,'Stored Gemini reason | Old 0.97 cutoff rejected result.',rid))
        self.conn.commit()
        return cid,rid

    def test_replay_95_percent_same_product_without_model_calls(self):
        name='Avène Hyaluron Activ B3 Triple Correction Eye Care Cream'
        cid,rid=self.make_case(name,'Avène',[
            ('bashrety',name),
            ('feel22','Avene Hyaluron Activ B3 Triple Correction Eye Care Cream')
        ],'same_product',.95)
        with patch('pipeline.product_identity_gemini.call_gemini_identity_batch',side_effect=AssertionError('model call prohibited')):
            preview=apply_saved_decisions(self.conn,dry_run=True)
            self.assertEqual(preview['automatically_verified'],1)
            self.assertTrue(preview['no_gemini_calls'])
            self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],'legacy')
            applied=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(applied['automatically_verified'],1)
        self.assertEqual(self.conn.execute('SELECT identity_status FROM canonical_products WHERE id=?',(cid,)).fetchone()[0],'verified')
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0],'confirmed')
        self.assertEqual(self.conn.execute('SELECT status FROM identity_gemini_queue WHERE review_id=?',(rid,)).fetchone()[0],'auto_resolved')
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['stored_decisions_checked'],0)

    def test_90_percent_same_product_can_apply_when_gates_pass(self):
        name='CeraVe Moisturizing Cream'
        cid,rid=self.make_case(name,'CeraVe',[
            ('sourcebeauty',name),('feel22',name)],'same_product',.90)
        applied=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(applied['automatically_verified'],1)

    def test_confident_different_product_splits_explicit_nuxe_variants(self):
        name='NUXE Huile Prodigieuse Dry Oil'
        cid,rid=self.make_case(name,'NUXE',[
            ('bashrety',name),
            ('feel22','NUXE Huile Prodigieuse Florale Dry Oil'),
            ('bloompharmacy','NUXE Huile Prodigieuse Golden Dry Oil')
        ],'different_product',.98)
        preview=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(preview['automatically_split'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        done=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(done['automatically_split'],1)
        records=self.conn.execute("SELECT canonical_product_id FROM products WHERE title LIKE ?",('%Prodigieuse%',)).fetchall()
        self.assertEqual(len({r[0] for r in records}),3)
        new=self.conn.execute("SELECT identity_status,catalog_visible FROM canonical_products WHERE id!=?",(cid,)).fetchall()
        self.assertTrue(all(r[0]=='provisional' and r[1]==0 for r in new))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_identity_constraints').fetchone()[0],2)
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['stored_decisions_checked'],0)

    def test_vacation_six_retailer_listings_become_two_formulation_groups(self):
        title='Vacation Sunscreen Lotion Spray 200ml'
        cid,rid=self.make_case(title,'Vacation',[
            ('sourcebeauty','Vacation Sunscreen Lotion'),
            ('sourcebeauty','Vacation Sunscreen Spray'),
            ('feel22','Vacation Sunscreen Lotion 120ml'),
            ('feel22','Vacation Sunscreen Lotion Spray 200ml'),
            ('drdawood','Vacation Sunscreen Lotion 120ml 1+1'),
            ('drdawood','Vacation Sunscreen Lotion 120ml')
        ],'different_product',.90)
        from pipeline.saved_gemini_decisions import plan_distinct_split,_case,_answer
        row=self.conn.execute("""SELECT r.*,q.verdict,q.confidence,q.explanation,q.model_name,
            q.evidence_hash queued_hash FROM product_review_queue r
            JOIN identity_gemini_queue q ON q.review_id=r.id WHERE r.id=?""",(rid,)).fetchone()
        plan,why=plan_distinct_split(self.conn,_case(row),_answer(row))
        self.assertIsNotNone(plan,why)
        self.assertEqual(plan['group_count'],2)
        self.assertEqual(sorted(g['variant'] for g in plan['groups']),
                         [['delivery_lotion'],['delivery_spray']])
        self.assertEqual([len(g['product_ids']) for g in plan['groups']],[4,2])
        self.assertEqual(len(plan['moves']),1)
        self.assertEqual(len(plan['keep_ids']),2)
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        preview=apply_saved_decisions(self.conn,dry_run=True)
        self.assertEqual(preview['automatically_split'],1)
        self.assertEqual(preview['new_canonical_products'],1)
        self.assertEqual(len(preview['sample_results'][0]['proposed_groups']),2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before)
        applied=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(applied['automatically_split'],1)
        self.assertEqual(applied['new_canonical_products'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before+1)
        kept=self.conn.execute('SELECT title FROM products WHERE canonical_product_id=? ORDER BY id',(cid,)).fetchall()
        self.assertEqual(len(kept),2)
        self.assertTrue(all('Spray' in r['title'] for r in kept))
        moved=self.conn.execute('SELECT title FROM products WHERE canonical_product_id!=? ORDER BY id',(cid,)).fetchall()
        self.assertEqual(len(moved),4)
        self.assertTrue(all('Spray' not in r['title'] for r in moved))
        self.assertEqual(apply_saved_decisions(self.conn,dry_run=False)['stored_decisions_checked'],0)

    def test_stale_vacation_source_offer_prevents_unsafe_partition(self):
        title='Vacation Sunscreen Lotion Spray 200ml'
        cid,rid=self.make_case(title,'Vacation',[
            ('sourcebeauty','Vacation Sunscreen Lotion'),
            ('sourcebeauty','Vacation Sunscreen Spray'),
            ('feel22','Vacation Sunscreen Lotion 120ml'),
            ('feel22','Vacation Sunscreen Lotion Spray 200ml')
        ],'different_product',.99)
        self.conn.execute("UPDATE products SET title='Vacation Sunscreen SPF 50 Spray' WHERE canonical_product_id=? AND title='Vacation Sunscreen Spray'",(cid,))
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['automatically_split'],0)
        self.assertEqual(result['still_needs_review'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0],'pending')

    def test_confident_negative_without_safe_partition_stays_structural_review(self):
        name='NUXE Generic Body Oil'
        cid,rid=self.make_case(name,'NUXE',[
            ('store1','NUXE Generic Body Oil'),
            ('store2','NUXE Unknown Different Product')
        ],'different_product',1.0)
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['automatically_split'],0)
        self.assertEqual(result['still_needs_review'],1)
        self.assertEqual(self.conn.execute('SELECT status FROM product_review_queue WHERE id=?',(rid,)).fetchone()[0],'pending')

    def test_gemini_history_filters_sorts_and_shows_applied_records(self):
        from routers.aggregation import gemini_identity_results

        class Proxy:
            def __init__(self,connection):self.connection=connection
            def __getattr__(self,key):return getattr(self.connection,key)
            def close(self):pass

        _,avene=self.make_case('Avène Hyaluron Activ B3 Eye Cream','Avène',
            [('bashrety','Avène Hyaluron Activ B3 Eye Cream'),
             ('feel22','Avene Hyaluron Activ B3 Eye Cream')],'same_product',.95)
        _,nuxe=self.make_case('NUXE Huile Prodigieuse Dry Oil','NUXE',
            [('bashrety','NUXE Huile Prodigieuse Dry Oil'),
             ('feel22','NUXE Huile Prodigieuse Florale Dry Oil')],'different_product',.99)
        _,cerave=self.make_case('CeraVe Hydrating Cleanser','CeraVe',
            [('sourcebeauty','CeraVe Hydrating Cleanser'),
             ('feel22','CeraVe Hydrating Cleanser')],'same_product',.80)

        self.conn.execute("UPDATE product_review_queue SET status='confirmed',decision=? WHERE id=?",
            (json.dumps({'action':'auto_verify'}),avene))
        self.conn.execute("UPDATE identity_gemini_queue SET status='auto_resolved' WHERE review_id=?",(avene,))

        defaults=dict(verdict=None,outcome=None,review_status=None,
            confidence_min=0,confidence_max=1,search='',sort_by='confidence',
            sort_order='desc',limit=50,offset=0)
        with patch('routers.aggregation.get_connection',return_value=Proxy(self.conn)):
            all_rows=gemini_identity_results(**defaults)
            self.assertEqual(all_rows['total'],3)
            self.assertEqual([r['review_id'] for r in all_rows['results']],[nuxe,avene,cerave])
            self.assertEqual(all_rows['results'][0]['model_reason'],'Stored Gemini reason')
            self.assertIn('Old 0.97 cutoff',all_rows['results'][0]['application_reason'])
            self.assertEqual(all_rows['results'][1]['decision']['action'],'auto_verify')
            self.assertEqual(all_rows['results'][1]['outcome'],'auto_resolved')
            self.assertEqual(len(all_rows['results'][0]['offers']),2)
            self.assertTrue(all_rows['results'][0]['evidence_current'])
            range_rows=gemini_identity_results(**{**defaults,'confidence_min':0.90,'confidence_max':0.98})
            self.assertEqual([r['review_id'] for r in range_rows['results']],[avene])
            high_different=gemini_identity_results(**{**defaults,'verdict':'different_product',
                'confidence_min':0.97})
            self.assertEqual([r['review_id'] for r in high_different['results']],[nuxe])
            completed=gemini_identity_results(**{**defaults,'review_status':'confirmed'})
            self.assertEqual([r['review_id'] for r in completed['results']],[avene])
            search=gemini_identity_results(**{**defaults,'search':'Hydrating Cleanser'})
            self.assertEqual(search['results'][0]['review_id'],cerave)
            ascending=gemini_identity_results(**{**defaults,'sort_order':'asc','limit':2})
            self.assertEqual([r['review_id'] for r in ascending['results']],[cerave,avene])
            second_page=gemini_identity_results(**{**defaults,'offset':1,'limit':1})
            self.assertEqual([r['review_id'] for r in second_page['results']],[avene])

    def test_history_rejects_invalid_confidence_range(self):
        from fastapi import HTTPException
        from routers.aggregation import gemini_identity_results
        with self.assertRaises(HTTPException) as captured:
            gemini_identity_results(verdict=None,outcome=None,review_status=None,
                confidence_min=.99,confidence_max=.90,search='',sort_by='confidence',
                sort_order='desc',limit=50,offset=0)
        self.assertEqual(captured.exception.status_code,422)

    def test_new_canonical_counter_includes_splits_after_first_25_cases(self):
        """Regression: 4 split cases shown while 0 new canonicals displayed."""
        for i in range(25):
            name=f'CeraVe Example Moisturizing Cream {i}'
            self.make_case(name,'CeraVe',[
                ('sourcebeauty',name),('feel22',name)
            ],'different_product',.95)
        original_count=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        name='NUXE Huile Prodigieuse Dry Oil'
        self.make_case(name,'NUXE',[
            ('bashrety',name),
            ('feel22','NUXE Huile Prodigieuse Florale Dry Oil'),
            ('bloompharmacy','NUXE Huile Prodigieuse Golden Dry Oil')
        ],'different_product',.99)
        preview=apply_saved_decisions(self.conn,limit=100,dry_run=True)
        self.assertGreaterEqual(preview['stored_decisions_checked'],26)
        self.assertEqual(preview['automatically_split'],1)
        self.assertEqual(preview['new_canonical_products'],2)
        self.assertGreaterEqual(preview['still_needs_review'],25)
        self.assertEqual(preview['stored_decisions_checked'],
            preview['automatically_verified']+preview['automatically_split']+preview['still_needs_review'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],original_count+1)
        applied=apply_saved_decisions(self.conn,limit=100,dry_run=False)
        self.assertEqual(applied['automatically_split'],1)
        self.assertEqual(applied['new_canonical_products'],2)
        self.assertGreaterEqual(applied['still_needs_review'],25)
        self.assertEqual(applied['stored_decisions_checked'],
            applied['automatically_verified']+applied['automatically_split']+applied['still_needs_review'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],original_count+3)

    def test_stale_case_cannot_apply_saved_confidence(self):
        name='CeraVe Moisturizing Cream'
        cid,rid=self.make_case(name,'CeraVe',[('a',name),('b',name)],'same_product',.95)
        self.conn.execute("UPDATE product_review_queue SET evidence_hash='changed' WHERE id=?",(rid,))
        result=apply_saved_decisions(self.conn,dry_run=False)
        self.assertEqual(result['automatically_verified'],0)
        self.assertEqual(result['still_needs_review'],1)


if __name__=='__main__':
    unittest.main()
