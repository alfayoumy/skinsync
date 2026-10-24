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

    def test_runs_all_enabled_sources_and_reports_sources_without_urls(self):
        def product_fetch(domain,page):
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
        def product_fetch(domain,page):
            if domain=='sourcebeauty.com':raise RuntimeError('upstream outage')
            return []
        reports=self._worker(['sourcebeauty','feel22'],product_fetch)
        self.assertEqual([r['status'] for r in reports],['failed','completed'])
        self.assertIn('upstream outage',reports[0]['reason'])


if __name__=='__main__':
    unittest.main()
