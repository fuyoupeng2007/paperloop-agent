"""Offline regressions for the official CLI bridge and resumable translation batches.

No real CLI, credentials, remote model, or application database is used.
"""
import base64
import copy
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import app as api
from backend import codex_bridge, parser, provider, store, worker
from tests.test_worker_regressions import block, document


class CodexBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = os.environ.get('PAPERLOOP_TEST_WORK')
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='paperloop-bridge-test-', dir=directory)
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        for replacement in (
            patch.object(store, 'DATA', self.data),
            patch.object(codex_bridge, 'executable', return_value='C:/Test Tools/codex.exe'),
            patch.object(codex_bridge, '_status_cache', (0, None)),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_cli_uses_argument_list_stdin_schema_and_output_file(self):
        captured = {}
        source = 'A result with $(unsafe); & shell characters is document data.'

        def run(args, **kwargs):
            captured.update(args=args, options=kwargs)
            self.assertIsInstance(args, list)
            self.assertFalse(kwargs.get('shell', False))
            self.assertEqual(args[:2], ['C:/Test Tools/codex.exe', 'exec'])
            self.assertEqual(args[-1], '-')
            self.assertNotIn(source, args)
            self.assertIn(source, kwargs['input'])
            self.assertIn('--ephemeral', args)
            self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
            self.assertIn('features.shell_tool=false', args)
            schema_file = Path(args[args.index('--output-schema') + 1])
            schema = json.loads(schema_file.read_text(encoding='utf-8'))
            self.assertEqual(schema['required'], ['translation'])
            self.assertFalse(schema['additionalProperties'])
            output = Path(args[args.index('-o') + 1])
            captured['job'] = output.parent
            output.write_text(json.dumps({'translation': '这是来自指定结果文件的译文。'}), encoding='utf-8')
            events = '\n'.join([
                'non-json diagnostic',
                json.dumps({'type': 'item.completed', 'item': {'text': 'Do not use stdout as the answer'}}),
                json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 120, 'output_tokens': 35}}),
            ])
            return subprocess.CompletedProcess(args, 0, stdout=events, stderr='')

        messages = [{'role': 'user', 'content': json.dumps({'task': 'translate', 'source': source})}]
        with patch.object(codex_bridge, 'status', return_value={'logged_in': True}), \
                patch.object(codex_bridge.subprocess, 'run', side_effect=run) as invocation:
            content, usage = codex_bridge.request(messages, timeout=19)
        self.assertEqual(json.loads(content), {'translation': '这是来自指定结果文件的译文。'})
        self.assertEqual(usage, {'input_tokens': 120, 'output_tokens': 35})
        self.assertEqual(captured['options']['timeout'], 19)
        self.assertFalse(captured['job'].exists(), 'Request files should be cleaned after completion')
        invocation.assert_called_once()

    def test_unlogged_request_has_friendly_error_without_spawning(self):
        with patch.object(codex_bridge, 'status', return_value={'logged_in': False}), \
                patch.object(codex_bridge.subprocess, 'run') as invocation:
            with self.assertRaisesRegex(codex_bridge.EngineError, 'ChatGPT.*登录'):
                codex_bridge.request([{'role': 'user', 'content': 'Read this'}])
        invocation.assert_not_called()
        self.assertFalse((self.data / 'engine').exists())

    def test_login_status_requires_chatgpt_login_not_just_success_exit(self):
        for output, expected in [('Logged in using ChatGPT', True), ('Logged in using an API key', False)]:
            with self.subTest(output=output), patch.object(codex_bridge.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, stdout=output, stderr='')) as invocation:
                result = codex_bridge.status(force=True)
                self.assertTrue(result['available'])
                self.assertEqual(result['logged_in'], expected)
                args, options = invocation.call_args
                self.assertEqual(args[0][1:], ['login', 'status'])
                self.assertFalse(options.get('shell', False))

    def test_visual_input_is_a_temporary_image_file_and_is_cleaned(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1kAAAAASUVORK5CYII=')
        captured = []

        def run(args, **kwargs):
            image = Path(args[args.index('--image') + 1])
            captured.append(image)
            self.assertEqual(image.read_bytes(), png)
            self.assertTrue(image.is_relative_to(self.data))
            self.assertNotIn('data:image/png;base64,', kwargs['input'])
            schema = json.loads(Path(args[args.index('--output-schema') + 1]).read_text(encoding='utf-8'))
            self.assertEqual(set(schema['required']), {'answer', 'citations', 'supplement'})
            Path(args[args.index('-o') + 1]).write_text(json.dumps({'answer': '图像解释。', 'citations': [], 'supplement': ''}), encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

        messages = [{'role': 'user', 'content': [
            {'type': 'text', 'text': json.dumps({'task': 'visual', 'question': 'Explain this figure'})},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}},
        ]}]
        with patch.object(codex_bridge, 'status', return_value={'logged_in': True}), \
                patch.object(codex_bridge.subprocess, 'run', side_effect=run):
            content, usage = codex_bridge.request(messages)
        self.assertEqual(json.loads(content)['answer'], '图像解释。')
        self.assertEqual(usage, {'input_tokens': 0, 'output_tokens': 0})
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].exists())

    def test_bridge_environment_does_not_inherit_another_session_or_api_key(self):
        with patch.dict(os.environ, {'CODEX_HOME': 'test-home', 'CODEX_THREAD_ID': 'private-session',
                                     'CODEX_IPC_TOKEN': 'private-ipc', 'OPENAI_API_KEY': 'private-key'}):
            environment = codex_bridge.environment()
        self.assertEqual(environment['CODEX_HOME'], 'test-home')
        self.assertNotIn('CODEX_THREAD_ID', environment)
        self.assertNotIn('CODEX_IPC_TOKEN', environment)
        self.assertNotIn('OPENAI_API_KEY', environment)


class CodexBatchTests(unittest.TestCase):
    def setUp(self):
        self.doc = document()
        self.doc['usage'] = {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'estimated_cost': 0}
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
                candidate = copy.deepcopy(self.doc)
                operation(candidate)
                self.doc = candidate
                return copy.deepcopy(candidate)

        replacements = [
            patch.object(store, 'get', side_effect=get),
            patch.object(store, 'change', side_effect=change),
            patch.object(store, 'all_docs', side_effect=lambda: [get(self.doc['id'])]),
            patch.object(store, 'settings', return_value={'provider': 'codex', 'call_limit': 20, 'vision_model': ''}),
            patch.object(codex_bridge, 'status', return_value={'available': True, 'logged_in': True}),
            patch.object(codex_bridge, 'request', side_effect=AssertionError('Real CLI requests are forbidden')),
            patch.object(provider.httpx, 'post', side_effect=AssertionError('Network requests are forbidden')),
            patch.object(provider, 'translate', side_effect=AssertionError('Batch path must not repeat single blocks')),
            patch.object(parser, 'parse_page', side_effect=AssertionError('Unexpected PDF access')),
            patch.object(worker, 'STOP', self.stop),
            patch.object(worker, 'WAKE', self.wake),
            patch.object(worker, 'CONTROL_LOCK', threading.RLock()),
            patch.object(worker, 'BUSY_DOCS', set()),
        ]
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_codex_provider_records_usage_and_shares_connection_for_images(self):
        codex_bridge.request.side_effect = None
        codex_bridge.request.return_value = ('{"answer":"已读取"}', {'input_tokens': 70, 'output_tokens': 12})
        result = provider.complete(self.doc['id'], [{'role': 'user', 'content': 'vision request'}], vision=True)
        self.assertEqual(result, '{"answer":"已读取"}')
        self.assertEqual(self.doc['usage'], {'calls': 1, 'input_tokens': 70, 'output_tokens': 12, 'estimated_cost': 0})
        codex_bridge.request.assert_called_once()
        provider.httpx.post.assert_not_called()

    def test_missing_ids_and_changed_numbers_fail_individually_without_repeating_success(self):
        self.doc['blocks'] = [
            block('good', kind='title', text='A study of 30 papers.'),
            block('missing', order=1, text='The method has 2 stages.'),
            block('wrong-number', order=2, text='The score is 95 percent.'),
        ]
        batches = []

        def complete(doc_id, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            batches.append([b['id'] for b in payload['blocks']])
            return json.dumps({'translations': [
                {'id': 'good', 'translation': '一项针对 30 篇论文的研究。'},
                {'id': 'wrong-number', 'translation': '得分为 96%。'},
            ]})

        with patch.object(provider, 'complete', side_effect=complete):
            worker.process(self.doc['id'])
        saved = store.get(self.doc['id'])
        by_id = {b['id']: b for b in saved['blocks']}
        self.assertEqual(saved['status'], 'review')
        self.assertEqual(by_id['good']['status'], 'done')
        self.assertEqual(by_id['good']['attempts'], 1)
        self.assertEqual(saved['title_translation'], '一项针对 30 篇论文的研究。')
        for block_id in ('missing', 'wrong-number'):
            self.assertEqual(by_id[block_id]['status'], 'failed')
            self.assertEqual(by_id[block_id]['translation'], '')
            self.assertEqual(by_id[block_id]['attempts'], 3)
        self.assertIn('数字', by_id['wrong-number']['error'])
        self.assertEqual(batches, [['good', 'missing', 'wrong-number'], ['missing', 'wrong-number'], ['missing', 'wrong-number']])
        # Continuing a finished/failed document must not silently send successful blocks again.
        api.task(self.doc['id'], api.Action(action='resume'))
        with patch.object(provider, 'complete', side_effect=AssertionError('No eligible blocks should be sent')) as complete:
            worker.process(self.doc['id'])
            complete.assert_not_called()
        self.assertEqual(self.doc['blocks'][0]['translation'], '一项针对 30 篇论文的研究。')

    def test_pause_saves_current_batch_then_stops_and_resumes_remaining_blocks_only(self):
        self.doc['blocks'] = [block(f'b{i}', order=i, text=f'This block reports result {i}.') for i in range(13)]
        entered = threading.Event()
        release = threading.Event()
        batches = []

        def complete(doc_id, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            ids = [b['id'] for b in payload['blocks']]
            batches.append(ids)
            if len(batches) == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError('The batch was not released by the test')
            return json.dumps({'translations': [{'id': block_id, 'translation': f'本段结果为 {block_id[1:]}。'} for block_id in ids]})

        with patch.object(provider, 'complete', side_effect=complete):
            thread = threading.Thread(target=worker.run, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(3), 'The first batch did not start')
                self.assertTrue(worker.is_busy(self.doc['id']))
                api.task(self.doc['id'], api.Action(action='pause'))
                release.set()
                deadline = time.monotonic() + 3
                while worker.is_busy(self.doc['id']) and time.monotonic() < deadline:
                    self.stop.wait(.01)
                self.assertFalse(worker.is_busy(self.doc['id']))
                saved = store.get(self.doc['id'])
                self.assertEqual(saved['status'], 'paused')
                self.assertEqual([b['status'] for b in saved['blocks']], ['done'] * 12 + ['pending'])
                self.assertEqual(saved['blocks'][-1]['attempts'], 0)
                self.assertEqual(len(batches), 1)
            finally:
                release.set()
                self.stop.set()
                self.wake.set()
                thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.stop.clear()
            api.task(self.doc['id'], api.Action(action='resume'))
            worker.process(self.doc['id'])
        self.assertEqual(batches, [[f'b{i}' for i in range(12)], ['b12']])
        self.assertEqual(self.doc['status'], 'completed')
        self.assertTrue(all(b['status'] == 'done' and b['attempts'] == 1 for b in self.doc['blocks']))

    def test_configuration_failure_releases_batch_without_spending_retry_attempt(self):
        self.doc['blocks'] = [block('first'), block('second', order=1)]
        with patch.object(provider, 'complete', side_effect=provider.ConfigurationError('请先登录 ChatGPT。')):
            with self.assertRaises(provider.ConfigurationError):
                worker.process(self.doc['id'])
        self.assertEqual([(b['status'], b['attempts']) for b in self.doc['blocks']], [('pending', 0), ('pending', 0)])


if __name__ == '__main__':
    unittest.main()
