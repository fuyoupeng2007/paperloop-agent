"""HTTP-to-agent integration with isolated paper storage and offline decisions.

Tests do not start the application lifespan, translation worker, or a real
ChatGPT process. Local tools and persisted source evidence stay real.
"""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend import app as api
from backend import agent_loop, codex_bridge, provider, store


HEADERS = {'X-PaperLoop': '1'}


def paper(doc_id='selected'):
    return {
        'id': doc_id, 'fingerprint': 'test-' + doc_id, 'title': 'A local reading experiment',
        'filename': doc_id + '.pdf', 'page_count': 1, 'status': 'completed',
        'blocks': [
            {'id': doc_id + '-result', 'page': 1, 'order': 0, 'kind': 'text', 'status': 'done',
             'text': 'The trial evaluated 30 papers and reported a 12 percent improvement.',
             'translation': '试验评估了 30 篇论文，并报告了 12% 的改善。',
             'bbox': [.1, .1, .9, .2], 'section': 'Results'},
            {'id': doc_id + '-table', 'page': 1, 'order': 1, 'kind': 'table', 'status': 'done',
             'text': 'Method | Papers\nLocal | 30', 'translation': '方法 | 论文数\n本地 | 30',
             'bbox': [.1, .3, .9, .5], 'section': 'Results', 'cells': [['Method', 'Papers'], ['Local', '30']]},
            {'id': doc_id + '-caption', 'page': 1, 'order': 2, 'kind': 'caption', 'status': 'done',
             'text': 'Table 1. Evaluated papers.', 'translation': '表 1：评估的论文。',
             'bbox': [.1, .52, .9, .56], 'section': 'Results'},
        ],
        'pages': [{'number': 1, 'status': 'done', 'warning': ''}],
        'notes': [{'id': 'existing-note', 'text': 'Preserve my manually verified reading note.', 'page': 1}],
        'chats': [{'id': 'existing-chat', 'question': 'Prior question', 'answer': 'Prior answer', 'citations': []}],
        'events': [], 'reading_page': 1, 'glossary': [], 'research': {},
        'usage': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'estimated_cost': 0},
    }


class AgentAPI(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[3] / 'work'
        work.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='agent-api-test-', dir=work)
        self.assertEqual(Path(temporary.name).resolve().parent, work.resolve())
        self.addCleanup(temporary.cleanup)
        data = patch.object(store, 'DATA', Path(temporary.name))
        data.start()
        self.addCleanup(data.stop)
        store.init()
        for doc_id in ('selected', 'unselected'):
            store.insert(paper(doc_id))
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with (store.DATA / (doc_id + '.pdf')).open('wb') as output:
                writer.write(output)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        status = patch.object(codex_bridge, 'status', return_value={'logged_in': True})
        status.start()
        self.addCleanup(status.stop)

    def wait_terminal(self, run_id, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get('/api/documents/selected/agent-runs/' + run_id)
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()
            if run['status'] != 'running' and 'selected' not in agent_loop.ACTIVE:
                return run
            time.sleep(.02)
        self.fail('Agent did not reach a terminal/boundary state: ' + json.dumps(run, ensure_ascii=False))

    def run_research(self, goal='核对论文实验样本量', invalid_first=False):
        decisions = []

        def complete(doc_id, messages, **kwargs):
            self.assertEqual(doc_id, 'selected')
            payload = json.loads(messages[-1]['content'])
            if payload['task'] == 'agent_review':
                return json.dumps({'goal_met': True, 'checks': [
                    {'index': i, 'supported': True, 'reason': '样本量与已读取原文一致。'}
                    for i, _ in enumerate(payload['claims'])], 'missing': []})
            self.assertEqual(payload['task'], 'agent_decision')
            decisions.append(payload)
            if invalid_first and len(decisions) == 1:
                return json.dumps({'action': 'read_blocks', 'reason': '读取待核对的段落',
                                   'block_ids': ['unselected-result'], 'plan': ['找到原文', '核对证据'],
                                   'open_questions': ['样本量是多少？']})
            if not payload['evidence']:
                return json.dumps({'action': 'search_paper', 'reason': '改用当前论文关键词检索',
                                   'query': 'trial evaluated', 'plan': ['检索原文', '核对证据'],
                                   'open_questions': ['样本量是多少？']})
            return json.dumps({'action': 'finish', 'reason': '依据原文形成结论',
                               'claims': [{'text': '论文评估了 30 篇论文。',
                                           'evidence_ids': [payload['evidence'][0]['id']]}],
                               'limitations': ['结论仅覆盖上传论文中已读取的实验描述。']})

        with patch.object(provider, 'complete', side_effect=complete):
            response = self.client.post('/api/documents/selected/agent-runs', headers=HEADERS,
                                        json={'goal': goal, 'max_steps': 5, 'allow_web': False})
            self.assertEqual(response.status_code, 200, response.text)
            run = self.wait_terminal(response.json()['id'])
        return run, decisions

    def test_http_run_recovers_from_tool_error_and_exports_real_local_evidence(self):
        before = store.get('selected')
        other = store.get('unselected')
        run, decisions = self.run_research('核实样本量 <script>alert("x")</script>', invalid_first=True)
        self.assertEqual(run['status'], 'completed', run)
        self.assertEqual([s['action'] for s in run['steps']], ['read_blocks', 'search_paper', 'finish'])
        self.assertEqual(run['steps'][0]['status'], 'error')
        self.assertEqual(decisions[1]['observations'][0]['status'], 'error')
        self.assertEqual(run['evidence'][0]['quote'], before['blocks'][0]['text'])
        self.assertEqual(run['evidence'][0]['block_id'], 'selected-result')
        self.assertEqual(run['evidence'][0]['page'], 1)
        self.assertEqual(run['claims'][0]['evidence_ids'], [run['evidence'][0]['id']])
        self.assertTrue(run['review']['goal_met'])
        self.assertEqual(run['steps_used'], 3)
        self.assertEqual(run['calls_used'], 4)
        self.assertTrue(all('web_search' not in [t['name'] for t in d['tools']] for d in decisions))
        listing = self.client.get('/api/documents/selected/agent-runs').json()
        self.assertEqual([item['id'] for item in listing], [run['id']])
        self.assertEqual(self.client.get('/api/documents/unselected/agent-runs').json(), [])
        base = '/api/documents/selected/agent-runs/' + run['id']
        html = self.client.get(base + '/export?format=html')
        self.assertEqual(html.status_code, 200)
        self.assertIn('attachment;', html.headers['content-disposition'])
        self.assertIn('&lt;script&gt;', html.text)
        self.assertNotIn('<script>', html.text)
        self.assertIn(before['blocks'][0]['text'], html.text)
        self.assertIn(run['evidence'][0]['id'], html.text)
        markdown = self.client.get(base + '/export?format=md')
        self.assertEqual(markdown.status_code, 200)
        self.assertIn('PDF 第 1 页', markdown.text)
        self.assertIn('论文评估了 30 篇论文。', markdown.text)
        self.assertEqual(self.client.get(base + '/export?format=pdf').status_code, 400)
        self.assertEqual(store.get('selected'), before)
        self.assertEqual(store.get('unselected'), other)

    def test_run_routes_enforce_paper_ownership_in_read_control_and_export(self):
        run, _ = self.run_research()
        wrong = '/api/documents/unselected/agent-runs/' + run['id']
        for response in (
            self.client.get(wrong), self.client.get(wrong + '/export'),
            self.client.post(wrong + '/control', headers=HEADERS, json={'action': 'cancel'}),
        ):
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(store.agent_get(run['id'])['status'], 'completed')
        missing = self.client.get('/api/documents/selected/agent-runs/no-such-run')
        self.assertEqual(missing.status_code, 404)

    def test_invalid_goal_limits_and_cross_site_posts_never_launch_model(self):
        url = '/api/documents/selected/agent-runs'
        with patch.object(agent_loop, 'start') as launch:
            invalid = [
                {'goal': ''}, {'goal': 'x' * 2001}, {'goal': '有效的任务', 'max_steps': 2},
                {'goal': '有效的任务', 'max_steps': 17},
            ]
            for body in invalid:
                response = self.client.post(url, headers=HEADERS, json=body)
                self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.client.post(url, json={'goal': '核对样本量'}).status_code, 403)
            response = self.client.post(url, headers={**HEADERS, 'Origin': 'https://untrusted.example'},
                                        json={'goal': '核对样本量'})
            self.assertEqual(response.status_code, 403)
            launch.assert_not_called()
        self.assertEqual(store.agent_list('selected'), [])

    def test_capability_response_matches_available_connection_without_exposing_settings(self):
        response = self.client.get('/api/documents/selected/agent-capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertIn('web_search', [tool['name'] for tool in response.json()['tools']])
        store.save_settings({'provider': 'api', 'base_url': 'https://model.example/v1',
                             'model': 'test', 'vision_model': '', 'api_key': 'not-for-the-ui'})
        response = self.client.get('/api/documents/selected/agent-capabilities')
        self.assertEqual({tool['name'] for tool in response.json()['tools']},
                         {'search_paper', 'read_blocks', 'finish'})
        self.assertNotIn('not-for-the-ui', response.text)
        self.assertNotIn('model.example', response.text)

    def test_completed_task_cannot_resume_or_be_cancelled_into_a_new_status(self):
        run, _ = self.run_research()
        url = '/api/documents/selected/agent-runs/' + run['id'] + '/control'
        self.assertEqual(self.client.post(url, headers=HEADERS,
                                         json={'action': 'resume', 'max_steps': 8}).status_code, 409)
        cancelled = self.client.post(url, headers=HEADERS, json={'action': 'cancel'})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()['status'], 'completed')
        self.assertEqual(self.client.post(url, headers=HEADERS, json={'action': 'restart'}).status_code, 422)

    def test_http_resume_after_model_error_keeps_prior_trace_and_finishes_same_run(self):
        before = store.get('selected')
        with patch.object(provider, 'complete', side_effect=provider.ConfigurationError('测试模型暂不可用')):
            response = self.client.post('/api/documents/selected/agent-runs', headers=HEADERS,
                                        json={'goal': '核对样本量', 'max_steps': 5, 'allow_web': False})
            self.assertEqual(response.status_code, 200, response.text)
            failed = self.wait_terminal(response.json()['id'])
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['steps_used'], 1)
        self.assertEqual(failed['calls_used'], 1)

        def restored(doc_id, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            if payload['task'] == 'agent_review':
                return json.dumps({'goal_met': True, 'checks': [
                    {'index': 0, 'supported': True, 'reason': '原文直接报告样本量。'}], 'missing': []})
            if not payload['evidence']:
                self.assertEqual(payload['observations'][0]['index'], 1)
                return json.dumps({'action': 'read_blocks', 'block_ids': ['selected-result'],
                                   'reason': '恢复后继续读取证据'})
            return json.dumps({'action': 'finish', 'claims': [
                {'text': '论文评估了 30 篇论文。', 'evidence_ids': [payload['evidence'][0]['id']]}]})

        with patch.object(provider, 'complete', side_effect=restored):
            response = self.client.post('/api/documents/selected/agent-runs/' + failed['id'] + '/control',
                                        headers=HEADERS, json={'action': 'resume', 'max_steps': 6})
            self.assertEqual(response.status_code, 200, response.text)
            resumed = self.wait_terminal(failed['id'])
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertEqual(resumed['id'], failed['id'])
        self.assertEqual(resumed['steps'][0], failed['steps'][0])
        self.assertEqual(resumed['max_steps'], 6)
        self.assertEqual(resumed['steps_used'], 3)
        self.assertEqual(resumed['calls_used'], 4)
        self.assertEqual(store.get('selected'), before)


if __name__ == '__main__':
    unittest.main()
