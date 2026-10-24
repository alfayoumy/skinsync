import sqlite3
import unittest
from unittest.mock import MagicMock,patch
from pipeline.db_manager import init_db
from routers.workflows import _multisource_worker


class MultiSourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        init_db(conn=self.conn,seed_defaults=False)
        self.conn.execute("UPDATE sources SET enabled=1 WHERE id IN ('sourcebeauty','nivea_me','feel22')")
        self.conn.commit()
        self.addCleanup(self.conn.close)

    def _worker(self,ids,product_fetch):
        wrapper=MagicMock(wraps=self.conn)
        wrapper.close=MagicMock()
        with patch('routers.workflows.get_connection',return_value=wrapper), \
             patch('pipeline.harvester.fetch_shopify_page',side_effect=product_fetch), \
             patch('pipeline.task_tracker.update_task_progress') as progress, \
             patch('pipeline.task_tracker.complete_task') as done, \
             patch('pipeline.task_tracker.fail_task') as failed:
            _multisource_worker('test-task',ids,1,1,True,{})
        self.assertFalse(failed.called)
        self.assertTrue(done.called)
        self.assertEqual(progress.call_count,len(ids))
        return done.call_args.kwargs['metadata']['sources']


    def test_unlimited_discovery_scans_until_empty_and_preserves_dry_run(self):
        from routers.workflows import discover_shopify
        from unittest.mock import patch
        products=[
            {'id':500,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
             'handle':'foaming-cleanser','product_type':'Cleanser',
             'variants':[{'id':501,'price':'100','available':True}]},
            {'id':600,'title':'CeraVe Hydrating Cleanser','vendor':'CeraVe',
             'handle':'hydrating-cleanser','product_type':'Cleanser',
             'variants':[{'id':601,'price':'120','available':True}]},
        ]
        seen=[]
        def fetch(domain,page,**kwargs):
            seen.append(page)
            return [products[page-1]] if page<=2 else []
        initial=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        with patch('pipeline.harvester.fetch_shopify_page',side_effect=fetch),\
             patch('routers.workflows.time.sleep'):
            report=discover_shopify(self.conn,'sourcebeauty',start_page=1,pages=0,dry_run=True)
        self.assertEqual(seen,[1,2,3])
        self.assertEqual(report['pages_scanned'],2)
        self.assertEqual(report['fetched'],2)
        self.assertTrue(report['catalog_exhausted'])
        self.assertEqual(report['stop_reason'],'catalog_exhausted')
        self.assertEqual(report['next_page'],3)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],initial)

    def test_unlimited_scan_stops_on_repeated_page_not_false_catalog_completion(self):
        from routers.workflows import discover_shopify
        product={'id':500,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
            'handle':'foaming-cleanser','product_type':'Cleanser',
            'variants':[{'id':501,'price':'100','available':True}]}
        with patch('pipeline.harvester.fetch_shopify_page',return_value=[product]),\
             patch('routers.workflows.time.sleep'):
            report=discover_shopify(self.conn,'sourcebeauty',pages=0,dry_run=True)
        self.assertEqual(report['pages_scanned'],1)
        self.assertEqual(report['stop_reason'],'repeated_page')
        self.assertFalse(report['catalog_exhausted'])

    def test_unlimited_scan_reports_errors_instead_of_catalog_exhaustion(self):
        from routers.workflows import discover_shopify
        with patch('pipeline.harvester.fetch_shopify_page',side_effect=RuntimeError('429 retry exhausted')):
            with self.assertRaisesRegex(RuntimeError,'429'):
                discover_shopify(self.conn,'sourcebeauty',pages=0,dry_run=True)

    def test_unlimited_scan_honors_page_boundary_cancellation(self):
        from routers.workflows import discover_shopify, ScanCancelled
        from unittest.mock import patch
        product={'id':500,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
            'handle':'foaming-cleanser','product_type':'Cleanser',
            'variants':[{'id':501,'price':'100','available':True}]}
        checkpoints=[]
        with patch('pipeline.harvester.fetch_shopify_page',return_value=[product]) as fetch,\
             patch('routers.workflows.time.sleep'):
            with self.assertRaises(ScanCancelled):
                discover_shopify(self.conn,'sourcebeauty',pages=0,dry_run=True,
                    on_page=lambda x:checkpoints.append(x),
                    should_cancel=lambda:bool(checkpoints))
            fetch.assert_called_once()
        self.assertEqual(checkpoints[0]['page'],1)

    def test_page_limit_can_exceed_three_without_exhausting_source(self):
        from routers.workflows import discover_shopify
        product={'id':501,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
            'handle':'foaming-cleanser','product_type':'Cleanser',
            'variants':[{'id':601,'price':'100','available':True}]}
        def fetch(domain,page,**kwargs):
            return [dict(product,id=page+1000)]  # each page different
        with patch('pipeline.harvester.fetch_shopify_page',side_effect=fetch),\
             patch('routers.workflows.time.sleep'):
            report=discover_shopify(self.conn,'sourcebeauty',pages=7,dry_run=True)
        self.assertEqual(report['pages_scanned'],7)
        self.assertEqual(report['stop_reason'],'page_limit')

    def test_runs_all_enabled_sources_and_reports_sources_without_urls(self):
        def product_fetch(domain,page,**kwargs):
            if domain=='sourcebeauty.com':
                return [{'id':999,'title':'CeraVe Foaming Cleanser','vendor':'CeraVe',
                         'handle':'foaming-cleanser','product_type':'Cleanser',
                         'variants':[{'id':1000,'price':'100','available':True}]}]
            return []
        before=self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
        reports=self._worker(['sourcebeauty','nivea_me'],product_fetch)
        self.assertEqual([r['status'] for r in reports],['completed','needs_urls'])
        self.assertEqual(reports[0]['fetched'],1)
        self.assertIn('URLs',reports[1]['reason'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0],before)

    def test_failed_source_does_not_stop_next_source(self):
        def product_fetch(domain,page,**kwargs):
            if domain=='sourcebeauty.com':raise RuntimeError('upstream outage')
            return []
        reports=self._worker(['sourcebeauty','feel22'],product_fetch)
        self.assertEqual([r['status'] for r in reports],['failed','completed'])
        self.assertIn('upstream outage',reports[0]['reason'])


if __name__=='__main__':
    unittest.main()
