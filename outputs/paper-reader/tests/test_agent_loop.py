"""No-network agent loop tests with transactional in-memory persistence."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend import agent_loop, provider, store

RealThread = threading.Thread


def decision(action, query='', block_ids=None, claims=None, limitations=None):
    return {'action': action, 'reason': '核对当前缺口', 'plan': ['查找证据', '核对结论'],
            'open_questions': ['实验条件是什么？'], 'query': query, 'block_ids': block_ids or [],
            'object_id': '', 'claims': claims or [], 'limitations': limitations or []}


def claim(text='方法评估了 30 篇论文。', evidence_id='paper:first'):
    return {'text': text, 'evidence_ids': [evidence_id]}


def evidence(block_id='first'):
    text = 'Our method evaluates 30 papers.' if block_id == 'first' else 'Experiments use a single GPU.'
    return {'id': 'paper:' + block_id, 'type': 'paper', 'title': 'Source', 'quote': text,
            'block_id': block_id, 'page': 99}


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'id': 'doc', 'fingerprint': 'pdf-hash', 'title': 'Research paper', 'page_count': 2,
                    'blocks': [{'id': 'first', 'page': 1, 'kind': 'text', 'text': 'Our method evaluates 30 papers.'},
                               {'id': 'second', 'page': 2, 'kind': 'text', 'text': 'Experiments use a single GPU.'}],
                    'notes': [{'text': 'Keep my note.'}], 'chats': [], 'reading_page': 2}
        self.original = copy.deepcopy(self.doc)
        self.config = store.DEFAULT_SETTINGS | {'provider': 'api', 'base_url': 'https://example.org/v1', 'model': 'test', 'api_key': 'private-test-secret'}
        self.runs = {}
        self.memory_lock = threading.RLock()
        self.payloads = []
        self.tools = SimpleNamespace(capabilities=Mock(return_value=['search_paper', 'read_blocks', 'inspect_figure', 'web_search', 'finish']),
                                     initial_context=Mock(return_value={'title': self.doc['title']}), execute=Mock())
        def get(run_id):
            with self.memory_lock:
                return copy.deepcopy(self.runs[run_id])
        def insert(run):
            with self.memory_lock:
                self.runs[run['id']] = copy.deepcopy(run)
        def change(run_id, operation):
            with self.memory_lock:
                current = copy.deepcopy(self.runs[run_id])
                operation(current)
                self.runs[run_id] = current
                return copy.deepcopy(current)
        replacements = [patch.object(store, 'get', side_effect=lambda _: copy.deepcopy(self.doc)),
            patch.object(store, 'all_docs', side_effect=lambda: [copy.deepcopy(self.doc)]),
            patch.object(store, 'settings', side_effect=lambda: self.config.copy()),
            patch.object(store, 'agent_get', side_effect=get, create=True),
            patch.object(store, 'agent_insert', side_effect=insert, create=True),
            patch.object(store, 'agent_change', side_effect=change, create=True),
            patch.object(store, 'agent_list', side_effect=lambda doc_id: [copy.deepcopy(r) for r in self.runs.values() if r['doc_id'] == doc_id], create=True),
            patch.object(provider, 'configured', return_value=True), patch.object(agent_loop, 'ACTIVE', {}),
            patch.object(agent_loop, '_tools', return_value=self.tools)]
        for item in replacements:
            item.start()
            self.addCleanup(item.stop)

    def start(self, **kwargs):
        with patch.object(agent_loop.threading, 'Thread'):
            return agent_loop.start('doc', '核对方法、实验条件与证据。', **kwargs)

    def drive(self, run, decisions, reviews=None):
        choices, judgments = iter(decisions), iter(reviews or [])
        def complete(doc_id, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            self.payloads.append(payload)
            value = next(judgments) if payload['task'] == 'agent_review' else next(choices)
            return json.dumps(value)
        with patch.object(provider, 'complete', side_effect=complete):
            agent_loop.run_loop(run['id'], run['execution_token'])
        return store.agent_get(run['id'])

    def review(self, count=1, met=True):
        return {'goal_met': met, 'checks': [{'index': i, 'supported': True, 'reason': '原文支持。'} for i in range(count)],
                'missing': [] if met else ['尚未核对实验硬件。']}

    def test_model_changes_action_after_tool_failure_and_adds_evidence_after_review(self):
        self.tools.execute.side_effect = [ValueError('检索词没有匹配，请读取已知段落。'),
            {'summary': '方法原文', 'evidence': [evidence()]},
            {'summary': '硬件原文', 'evidence': [evidence('second')]}]
        run = self.start()
        result = self.drive(run, [decision('search_paper', 'wrong term'), decision('read_blocks', block_ids=['first']),
            decision('finish', claims=[claim()]), decision('read_blocks', block_ids=['second']),
            decision('finish', claims=[claim(), claim('实验使用一块 GPU。', 'paper:second')])],
            [self.review(met=False), self.review(count=2)])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([s['action'] for s in result['steps']], ['search_paper', 'read_blocks', 'finish', 'read_blocks', 'finish'])
        self.assertIn('检索词没有匹配', self.payloads[1]['observations'][0]['observation']['summary'])
        next_decision = [p for p in self.payloads if p['task'] == 'agent_decision'][3]
        self.assertIn('尚未核对实验硬件', next_decision['observations'][-1]['observation']['summary'])
        self.assertEqual(result['calls_used'], 7)
        self.assertEqual([e['page'] for e in result['evidence']], [1, 2])
        self.assertEqual(self.doc, self.original)
        self.assertFalse(agent_loop.ACTIVE)

    def test_fabricated_finish_evidence_is_rejected_then_model_can_repair(self):
        self.tools.execute.return_value = {'summary': 'Read', 'evidence': [evidence()]}
        result = self.drive(self.start(), [decision('finish', claims=[claim(evidence_id='invented')]),
            decision('read_blocks', block_ids=['first']), decision('finish', claims=[claim()])], [self.review()])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['steps'][0]['observation']['error'], 'invalid_evidence')
        self.assertEqual(len([p for p in self.payloads if p['task'] == 'agent_review']), 1)
        self.assertEqual(result['claims'], [claim()])

    def test_forged_quotes_wrong_document_and_private_web_are_not_registered(self):
        wrong = evidence() | {'quote': 'Invented 100 percent score.'}
        self.tools.execute.return_value = {'summary': 'Untrusted results', 'evidence': [wrong,
            evidence() | {'id': 'paper:other', 'block_id': 'outside-document'},
            {'id': 'web:x', 'type': 'web', 'title': 'private', 'quote': 'secret', 'url': 'http://127.0.0.1/secret'},
            {'id': 'visual:x', 'type': 'visual', 'title': 'bad', 'quote': 'picture', 'page': 3, 'box': [0, 0, 1, 1]}]}
        result = self.drive(self.start(max_steps=3), [decision('read_blocks', block_ids=['first']),
            decision('finish', claims=[claim()]), decision('finish', limitations=['未找到可核对证据。'])])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['claims'], [])
        self.assertIn('4 条来源', result['steps'][0]['observation']['summary'])

    def test_unsupported_review_is_not_silently_completed(self):
        self.tools.execute.return_value = {'summary': 'Read', 'evidence': [evidence()]}
        bad_review = {'goal_met': True, 'checks': [{'index': 0, 'supported': False, 'reason': '原文并未支持更强结论。'}], 'missing': []}
        result = self.drive(self.start(max_steps=3), [decision('read_blocks', block_ids=['first']),
            decision('finish', claims=[claim('该方法世界最好。')]), decision('finish', limitations=['证据不足。'])], [bad_review])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['claims'], [])
        self.assertFalse(result['review']['goal_met'])
        self.assertIn('原文并未支持', result['review']['missing'][0])

    def test_duplicate_or_missing_review_checks_do_not_pass(self):
        self.tools.execute.return_value = {'summary': 'Read', 'evidence': [evidence()]}
        duplicate = {'goal_met': True, 'checks': [{'index': 0, 'supported': True, 'reason': 'yes'}] * 2, 'missing': []}
        result = self.drive(self.start(max_steps=3), [decision('read_blocks', block_ids=['first']),
            decision('finish', claims=[claim()]), decision('finish', limitations=['复核不完整。'])], [duplicate])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['claims'], [])

    def test_review_with_goal_met_but_required_missing_is_not_complete(self):
        self.tools.execute.return_value = {'summary': 'Read', 'evidence': [evidence()]}
        review = self.review()
        review['missing'] = ['仍缺目标要求的硬件证据。']
        result = self.drive(self.start(max_steps=3), [decision('read_blocks', block_ids=['first']),
            decision('finish', claims=[claim()]), decision('finish', limitations=['硬件尚未核实。'])], [review])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertFalse(result['review']['goal_met'])
        self.assertEqual(result['claims'], [claim()])

    def test_partial_findings_survive_later_unsupported_claims_and_early_finish(self):
        self.tools.execute.return_value = {'summary': 'Read', 'evidence': [evidence(), evidence('second')]}
        first_review = {'goal_met': False, 'checks': [{'index': 0, 'supported': True, 'reason': '原文支持。'},
            {'index': 1, 'supported': False, 'reason': '原文不支持。'}], 'missing': ['尚未完成目标。']}
        second_review = {'goal_met': False, 'checks': [{'index': 0, 'supported': False, 'reason': '也不支持新的推断。'}], 'missing': []}
        result = self.drive(self.start(), [decision('read_blocks', block_ids=['first', 'second']),
            decision('finish', claims=[claim(), claim('使用一千块 GPU。', 'paper:second')]),
            decision('finish', claims=[claim('硬件决定结果最优。', 'paper:second')]),
            decision('finish', limitations=['无法从已有证据核实其余主张。'])], [first_review, second_review])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['claims'], [claim()])

    def test_repeated_action_guard_stops_before_third_identical_tool_call(self):
        self.tools.execute.return_value = {'summary': 'No match', 'evidence': []}
        result = self.drive(self.start(), [decision('search_paper', 'same')] * 3)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['stop_reason'], 'repeated_action')
        self.assertEqual(self.tools.execute.call_count, 2)
        self.assertEqual(result['steps_used'], 3)

    def test_invalid_actions_and_repeated_fabricated_finish_cannot_complete(self):
        run = self.start(max_steps=3)
        result = self.drive(run, [decision('run_shell')] * 3)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['stop_reason'], 'budget')
        self.tools.execute.assert_not_called()
        self.assertTrue(all(s['observation']['error'] == 'step_failed' for s in result['steps']))
        result = self.drive(self.start(), [decision('finish', claims=[claim(evidence_id='invented')])] * 3)
        self.assertEqual(result['stop_reason'], 'repeated_action')
        self.assertEqual(result['claims'], [])

    def test_step_budget_preserves_observations_and_never_auto_extends(self):
        self.tools.execute.return_value = {'summary': 'No answer yet', 'evidence': []}
        result = self.drive(self.start(max_steps=3), [decision('search_paper', str(i)) for i in range(3)])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['stop_reason'], 'budget')
        self.assertEqual(result['steps_used'], 3)
        self.assertEqual(result['calls_used'], 3)
        with self.assertRaises(HTTPException):
            agent_loop.control('doc', result['id'], 'resume')
        with patch.object(agent_loop.threading, 'Thread'):
            resumed = agent_loop.control('doc', result['id'], 'resume', max_steps=6)
        self.assertEqual(resumed['max_steps'], 6)
        self.assertEqual(resumed['max_calls'], 18)
        self.assertEqual(resumed['calls_used'], 3)

    def test_nested_model_calls_are_reserved_and_stop_at_run_budget(self):
        def execute(doc, action, args, allow_web, reserve_call):
            for _ in range(4):
                reserve_call()
            return {'summary': 'Tool request', 'evidence': []}
        self.tools.execute.side_effect = execute
        result = self.drive(self.start(max_steps=3), [decision('search_paper', 'one'), decision('search_paper', 'two')])
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['stop_reason'], 'budget')
        self.assertEqual(result['calls_used'], 9)
        self.assertEqual(result['steps_used'], 2)

    def inflight(self, action):
        run = self.start()
        entered, release = threading.Event(), threading.Event()
        def complete(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(4))
            return json.dumps(decision('read_blocks', block_ids=['first']))
        with patch.object(provider, 'complete', side_effect=complete):
            thread = RealThread(target=agent_loop.run_loop, args=(run['id'], run['execution_token']))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                pending = agent_loop.control('doc', run['id'], action)
                self.assertEqual(pending['status'], 'running')
                self.assertTrue(pending[action + '_requested'])
                with self.assertRaises(HTTPException):
                    agent_loop.control('doc', run['id'], 'resume')
            finally:
                release.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.tools.execute.assert_not_called()
        result = store.agent_get(run['id'])
        self.assertEqual(result['status'], 'paused' if action == 'pause' else 'cancelled')
        self.assertFalse(result[action + '_requested'])
        self.assertEqual(result['steps'][0]['status'], 'interrupted')
        self.assertEqual(result['calls_used'], 1)
        self.assertFalse(agent_loop.ACTIVE)

    def test_pause_during_model_call_prevents_next_tool_call(self):
        self.inflight('pause')

    def test_cancel_during_model_call_prevents_next_tool_call(self):
        self.inflight('cancel')

    def test_pause_and_cancel_keep_returned_tool_evidence_and_caveats(self):
        for action in ('pause', 'cancel'):
            with self.subTest(action=action):
                run = self.start()
                entered, release = threading.Event(), threading.Event()
                def execute(*args):
                    entered.set()
                    self.assertTrue(release.wait(4))
                    return {'summary': 'Returned evidence', 'evidence': [evidence()],
                            'limitations': ['来源须核对。'], 'truncated': True}
                self.tools.execute.side_effect = execute
                with patch.object(provider, 'complete', return_value=json.dumps(decision('read_blocks', block_ids=['first']))) as model:
                    thread = RealThread(target=agent_loop.run_loop, args=(run['id'], run['execution_token']))
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(3))
                        pending = agent_loop.control('doc', run['id'], action)
                        self.assertTrue(pending[action + '_requested'])
                    finally:
                        release.set()
                        thread.join(4)
                    model.assert_called_once()
                self.assertFalse(thread.is_alive())
                result = store.agent_get(run['id'])
                self.assertEqual(result['status'], 'paused' if action == 'pause' else 'cancelled')
                self.assertEqual(len(result['evidence']), 1)
                self.assertEqual(result['steps'][0]['status'], 'completed')
                self.assertIn('来源须核对。', result['steps'][0]['observation']['limitations'])
                self.assertEqual(len(result['steps'][0]['observation']['limitations']), 2)

    def test_restart_pauses_and_marks_inflight_step_without_respending(self):
        run = self.start()
        self.runs[run['id']]['steps'] = [{'index': 1, 'status': 'running'}]
        with patch.object(provider, 'complete') as model:
            agent_loop.recover_interrupted()
        model.assert_not_called()
        result = store.agent_get(run['id'])
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(result['stop_reason'], 'interrupted')
        self.assertEqual(result['steps'][0]['status'], 'interrupted')
        self.assertEqual(result['execution_token'], '')

    def test_restart_honors_already_requested_cancel(self):
        run = self.start()
        self.runs[run['id']]['cancel_requested'] = True
        agent_loop.recover_interrupted()
        self.assertEqual(store.agent_get(run['id'])['status'], 'cancelled')

    def test_final_status_commit_honors_pause_or_cancel_race(self):
        run = self.start()
        self.runs[run['id']]['pause_requested'] = True
        result = agent_loop._stop(run['id'], run['execution_token'], 'completed', 'goal_met', '')
        self.assertEqual(result['status'], 'paused')
        self.runs[run['id']].update(status='running', pause_requested=True, cancel_requested=True)
        result = agent_loop._stop(run['id'], run['execution_token'], 'completed', 'goal_met', '')
        self.assertEqual(result['status'], 'cancelled')

    def test_duplicate_start_wrong_document_control_and_stale_runner(self):
        run = self.start()
        with self.assertRaises(HTTPException):
            self.start()
        with self.assertRaises(HTTPException) as error:
            agent_loop.control('other', run['id'], 'cancel')
        self.assertEqual(error.exception.status_code, 404)
        old = copy.deepcopy(run)
        with patch.object(provider, 'complete') as model:
            agent_loop.run_loop(run['id'], 'stale-token')
        model.assert_not_called()
        self.assertEqual(store.agent_get(run['id']), old)
        self.assertEqual(agent_loop.ACTIVE['doc'], (run['id'], run['execution_token']))

    def test_provider_change_pauses_before_call_and_secret_is_not_stored(self):
        run = self.start()
        self.assertNotIn('private-test-secret', json.dumps(run))
        self.config['api_key'] = 'changed-key'
        with patch.object(provider, 'complete') as model:
            agent_loop.run_loop(run['id'], run['execution_token'])
        model.assert_not_called()
        result = store.agent_get(run['id'])
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(result['stop_reason'], 'provider_changed')
        self.assertEqual(result['calls_used'], 0)
        with self.assertRaises(HTTPException):
            agent_loop.control('doc', run['id'], 'resume')


if __name__ == '__main__':
    unittest.main()
