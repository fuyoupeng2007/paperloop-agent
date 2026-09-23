"""Worker race/recovery regressions; all documents and provider results stay in memory."""
import copy
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app as api
from backend import parser, provider, store, worker


def block(block_id='body', page=1, order=0, **values):
    return {
        'id': block_id, 'page': page, 'order': order,
        'text': 'The reading method improves comprehension.',
        'section': 'Introduction', 'kind': 'text', 'status': 'pending',
        'attempts': 0, 'translation': '', 'error': '',
        'bbox': [.1, .2, .9, .3], 'size': 11,
        **values,
    }


def document():
    return {
        'id': 'regression', 'status': 'queued', 'message': '',
        'title': 'Reading study', 'filename': 'study.pdf', 'page_count': 1,
        'pages': [{'number': 1, 'status': 'done', 'warning': '', 'source': 'text'}],
        'blocks': [block()], 'notes': [], 'chats': [], 'events': [],
        'glossary': [], 'glossary_ready': True, 'glossary_version': 1,
    }


class WorkerRegressions(unittest.TestCase):
    def setUp(self):
        self.doc = document()
        self.memory_lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()

        def get(doc_id):
            self.assertEqual(doc_id, self.doc['id'])
            with self.memory_lock:
                return copy.deepcopy(self.doc)

        def change(doc_id, operation):
            self.assertEqual(doc_id, self.doc['id'])
            with self.memory_lock:
                # Match SQLite transaction rollback when an operation raises.
                candidate = copy.deepcopy(self.doc)
                operation(candidate)
                self.doc = candidate
                return copy.deepcopy(candidate)

        replacements = [
            patch.object(store, 'get', side_effect=get),
            patch.object(store, 'change', side_effect=change),
            patch.object(store, 'all_docs', side_effect=lambda: [get(self.doc['id'])]),
            patch.object(store, 'settings', return_value={}),
            patch.object(provider, 'configured', return_value=True),
            patch.object(provider, 'translate', return_value='阅读方法改善理解。'),
            patch.object(provider, 'extract_glossary', return_value=[]),
            patch.object(provider, 'complete', side_effect=AssertionError('External model calls are forbidden')),
            patch.object(parser, 'parse_page', side_effect=AssertionError('Unexpected PDF access')),
            patch.object(worker, 'STOP', self.stop),
            patch.object(worker, 'WAKE', self.wake),
            patch.object(worker, 'CONTROL_LOCK', threading.RLock(), create=True),
            patch.object(worker, 'BUSY_DOCS', set(), create=True),
        ]
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_pause_keeps_inflight_result_but_rejects_mutations_until_settled(self):
        entered = threading.Event()
        release = threading.Event()

        def translating(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Test did not release the in-flight translation')
            return '当前请求完成后的译文。'

        provider.translate.side_effect = translating
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(3), 'Worker did not start translation')
            self.assertTrue(worker.is_busy(self.doc['id']))
            api.task(self.doc['id'], api.Action(action='pause'))
            for action in ('resume', 'retry', 'ocr'):
                with self.subTest(action=action):
                    with self.assertRaises(HTTPException) as rejected:
                        api.task(self.doc['id'], api.Action(action=action, page=1))
                    self.assertEqual(rejected.exception.status_code, 409)
            with self.assertRaises(HTTPException) as rejected:
                api.glossary(self.doc['id'], api.Glossary(terms=[api.Term(source='reading', target='精读')]))
            self.assertEqual(rejected.exception.status_code, 409)
            self.assertEqual(store.get(self.doc['id'])['status'], 'paused')
            release.set()
            deadline = time.monotonic() + 3
            while worker.is_busy(self.doc['id']) and time.monotonic() < deadline:
                self.stop.wait(.01)
            self.assertFalse(worker.is_busy(self.doc['id']))
            saved = store.get(self.doc['id'])
            self.assertEqual(saved['status'], 'paused')
            self.assertEqual(saved['blocks'][0]['translation'], '当前请求完成后的译文。')
            self.assertEqual(saved['blocks'][0]['status'], 'done')
            provider.translate.assert_called_once()
        finally:
            release.set()
            self.stop.set()
            self.wake.set()
            thread.join(timeout=4)
        self.assertFalse(thread.is_alive(), 'Worker thread leaked from test')
        # The same operation is accepted once the old worker relinquishes ownership.
        queued = api.task(self.doc['id'], api.Action(action='ocr', page=1))
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['pages'][0]['status'], 'failed')
        self.assertTrue(queued['pages'][0]['force_ocr'])

    def test_exhausted_pending_block_is_failed_and_not_reported_completed(self):
        self.doc['blocks'][0]['attempts'] = 3
        worker.process(self.doc['id'])
        saved = store.get(self.doc['id'])
        self.assertEqual(saved['status'], 'review')
        self.assertEqual(saved['blocks'][0]['status'], 'failed')
        provider.translate.assert_not_called()

    def test_restart_marks_interrupted_third_attempt_failed(self):
        self.doc['status'] = 'translating'
        self.doc['blocks'][0].update(status='translating', attempts=3)
        with patch.object(worker.threading, 'Thread') as thread:
            worker.start()
            thread.return_value.start.assert_called_once()
        saved = store.get(self.doc['id'])
        self.assertEqual(saved['status'], 'queued')
        self.assertEqual(saved['blocks'][0]['status'], 'failed')
        provider.translate.assert_not_called()

    def test_restart_preserves_finished_work_and_retries_unfinished_only(self):
        self.doc['status'] = 'translating'
        self.doc['blocks'] = [
            block('finished', status='done', translation='已完成译文。', attempts=1),
            block('interrupted', order=1, status='translating', attempts=1),
        ]
        with patch.object(worker.threading, 'Thread'):
            worker.start()
        self.assertEqual(store.get(self.doc['id'])['blocks'][1]['status'], 'pending')
        worker.process(self.doc['id'])
        saved = store.get(self.doc['id'])
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['blocks'][0]['translation'], '已完成译文。')
        provider.translate.assert_called_once()
        self.assertEqual(provider.translate.call_args.args[1]['id'], 'interrupted')

    def test_explicit_retry_can_recover_an_exhausted_block(self):
        self.doc['status'] = 'review'
        self.doc['blocks'][0].update(status='failed', attempts=3, error='Interrupted')
        api.task(self.doc['id'], api.Action(action='retry', block_id='body'))
        worker.process(self.doc['id'])
        saved = store.get(self.doc['id'])
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['blocks'][0]['status'], 'done')
        provider.translate.assert_called_once()

    def test_reparse_recomputes_chapters_without_inheriting_final_references(self):
        for heading in (None, 'Methods'):
            with self.subTest(new_heading=heading):
                self.doc = document()
                self.doc['page_count'] = 3
                self.doc['pages'] = [
                    {'number': 1, 'status': 'failed', 'warning': '', 'source': 'text'},
                    {'number': 2, 'status': 'done', 'warning': '', 'source': 'text'},
                    {'number': 3, 'status': 'done', 'warning': '', 'source': 'text'},
                ]
                self.doc['blocks'] = [
                    block('old-first'),
                    block('continuation', page=2, section='References', status='preserved'),
                    block('formula', page=2, order=1, kind='formula', status='preserved'),
                    block('margin', page=2, order=2, kind='margin', status='preserved'),
                    block('refs-heading', page=3, kind='heading', text='References', section='References', status='preserved'),
                    block('citation', page=3, order=1, section='References', status='preserved'),
                ]
                self.doc['notes'] = [{'id': 'n', 'block_id': 'old-first', 'page': 1, 'text': 'Saved note'}]
                self.doc['chats'] = [{'citations': [{'block_id': 'old-first', 'page': 1, 'quote': 'Old text'}]}]
                new_blocks = [block('new-first', section='')]
                if heading:
                    new_blocks.insert(0, block('new-heading', kind='heading', text=heading, section=''))
                    new_blocks[1]['order'] = 1
                parsed = {'number': 1, 'width': 612, 'height': 792, 'source': 'text', 'status': 'done', 'warning': '', 'blocks': new_blocks}
                parser.parse_page.side_effect = None
                parser.parse_page.return_value = parsed
                provider.configured.return_value = False
                worker.process(self.doc['id'])
                saved = store.get(self.doc['id'])
                by_id = {b['id']: b for b in saved['blocks']}
                self.assertNotEqual(by_id['new-first']['section'], 'References')
                self.assertEqual(by_id['new-first']['status'], 'pending')
                self.assertEqual(by_id['continuation']['section'], by_id['new-first']['section'])
                self.assertEqual(by_id['continuation']['status'], 'pending')
                if heading:
                    self.assertEqual(by_id['continuation']['section'], heading)
                for block_id in ('formula', 'margin', 'refs-heading', 'citation'):
                    self.assertEqual(by_id[block_id]['status'], 'preserved')
                self.assertTrue(saved['notes'][0]['stale'])
                self.assertTrue(saved['chats'][0]['citations'][0]['stale'])
        provider.translate.assert_not_called()

    def test_note_page_is_derived_from_linked_block(self):
        self.doc['page_count'] = 2
        for requested_page in (None, 2):
            with self.subTest(requested_page=requested_page):
                note = api.add_note(self.doc['id'], api.Note(text='Linked note', block_id='body', page=requested_page))
                self.assertEqual(note['page'], 1)
                self.assertEqual(store.get(self.doc['id'])['notes'][-1]['page'], 1)


if __name__ == '__main__':
    unittest.main()
