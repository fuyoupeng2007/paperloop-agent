"""Real local agent tools, isolated PDFs, and explicit offline model fixtures."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from backend import agent_tools, codex_bridge, parser, provider, store
from tests.test_agent_api import paper


class AgentTools(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[3] / 'work'
        work.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='agent-tools-test-', dir=work)
        self.assertEqual(Path(temporary.name).resolve().parent, work.resolve())
        self.addCleanup(temporary.cleanup)
        data = patch.object(store, 'DATA', Path(temporary.name))
        data.start()
        self.addCleanup(data.stop)
        store.init()
        self.doc = paper()
        store.insert(self.doc)
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with (store.DATA / (self.doc['id'] + '.pdf')).open('wb') as output:
            writer.write(output)
        for replacement in (
            patch.object(codex_bridge, 'status', return_value={'logged_in': True}),
            patch.object(codex_bridge, 'request', side_effect=AssertionError('Unexpected external model call')),
            patch.object(provider, 'complete', side_effect=AssertionError('Unexpected external model call')),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_search_translation_returns_literal_source_and_stable_evidence(self):
        before = copy.deepcopy(self.doc)
        first = agent_tools.execute(self.doc, 'search_paper', {'query': '试验 评估'})
        second = agent_tools.execute(self.doc, 'search_paper', {'query': 'trial evaluated'})
        self.assertTrue(first['evidence'])
        self.assertEqual(first['evidence'][0]['quote'], self.doc['blocks'][0]['text'])
        self.assertEqual(first['evidence'][0]['id'], second['evidence'][0]['id'])
        self.assertEqual(first['evidence'][0]['page'], 1)
        self.assertEqual(self.doc, before)
        self.assertEqual(store.get(self.doc['id']), before)

    def test_read_rejects_foreign_blocks_and_extra_authority_before_any_model(self):
        invalid = [
            ('read_blocks', {'block_ids': ['unselected-result']}),
            ('read_blocks', {'block_ids': ['selected-result'], 'doc_id': 'unselected'}),
            ('search_paper', {'query': 'trial', 'path': '../other.pdf'}),
            ('search_paper', {'query': 'trial', 'command': 'shell'}),
            ('execute_python', {'code': 'print(1)'}),
            ('web_search', {'query': 'trial'}),
        ]
        for action, args in invalid:
            with self.subTest(action=action, args=args), self.assertRaises(agent_tools.ToolError):
                agent_tools.execute(self.doc, action, args, allow_web=False)
        self.assertEqual(store.get(self.doc['id']), self.doc)

    def test_long_block_read_exposes_truncation_instead_of_claiming_full_coverage(self):
        self.doc['blocks'][0]['text'] = 'A sufficiently long source paragraph. ' * 300
        result = agent_tools.execute(self.doc, 'read_blocks', {'block_ids': ['selected-result']})
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['evidence']), 4)
        self.assertEqual(''.join(e['quote'] for e in result['evidence']), self.doc['blocks'][0]['text'][:7200])
        self.assertIn('7200', result['summary'])

    def test_real_layout_and_pdf_crop_are_available_to_visual_tool(self):
        context = agent_tools.initial_context(self.doc)
        self.assertEqual(len(context['objects']), 1)
        obj = context['objects'][0]
        self.assertEqual(obj['label'], '表 1')
        reserved = []
        with patch.object(provider, 'complete', return_value=json.dumps({'answer': '表格展示了样本量。'})) as model, \
                patch.object(parser, 'png_bytes', wraps=parser.png_bytes) as image:
            result = agent_tools.execute(self.doc, 'inspect_figure',
                                         {'object_id': obj['object_id'], 'query': '核对表头与样本量'},
                                         reserve_call=lambda: reserved.append(True))
        self.assertEqual(reserved, [True])
        self.assertEqual(image.call_args.args[0], store.DATA / 'selected.pdf')
        self.assertEqual(image.call_args.args[1], 1)
        self.assertTrue(model.call_args.kwargs['vision'])
        evidence = result['evidence'][0]
        self.assertEqual(evidence['type'], 'visual')
        self.assertEqual(evidence['object_id'], obj['object_id'])
        self.assertEqual(evidence['box'], [.1, .3, .9, .5])
        self.assertIn('需核对', evidence['title'])
        self.assertEqual(store.get(self.doc['id']), self.doc)

    def test_web_requires_actual_activity_and_preserves_billed_usage_on_failure(self):
        response = {'answer': 'Asserted finding', 'sources': [{'title': 'Source',
                    'url': 'https://example.edu/paper', 'claim': 'Reported 30 papers'}]}
        with patch.object(codex_bridge, 'request', return_value=(json.dumps(response),
                {'web_events': [], 'input_tokens': 13, 'output_tokens': 5})) as model:
            with self.assertRaises(provider.ProviderError):
                agent_tools.execute(self.doc, 'web_search', {'query': 'paper source'})
        self.assertTrue(model.call_args.kwargs['web_search'])
        after = store.get(self.doc['id'])
        self.assertEqual(after['usage']['calls'], 1)
        self.assertEqual(after['usage']['input_tokens'], 13)
        for key in ('blocks', 'notes', 'chats', 'reading_page'):
            self.assertEqual(after[key], self.doc[key])

    def test_web_sources_stay_external_and_private_links_are_removed(self):
        response = {'answer': '来源报告了论文样本量。', 'sources': [
            {'title': 'Source', 'url': 'https://example.edu/paper', 'claim': 'Reported 30 papers'},
            {'title': 'Local settings', 'url': 'http://127.0.0.1:8765/api/settings', 'claim': 'Invalid'},
        ], 'limitations': []}
        usage = {'web_events': [{'action': {'type': 'search', 'query': 'paper source'}}],
                 'input_tokens': 4, 'output_tokens': 3}
        with patch.object(codex_bridge, 'request', return_value=(json.dumps(response), usage)):
            result = agent_tools.execute(self.doc, 'web_search', {'query': 'paper source'})
        self.assertEqual(len(result['evidence']), 1)
        evidence = result['evidence'][0]
        self.assertEqual(evidence['type'], 'web')
        self.assertEqual(evidence['url'], 'https://example.edu/paper')
        self.assertNotIn('block_id', evidence)
        self.assertEqual(result['web_activity'], [{'type': 'search', 'query': 'paper source'}])
        self.assertTrue(result['limitations'])


if __name__ == '__main__':
    unittest.main()
