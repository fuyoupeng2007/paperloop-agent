"""Isolated storage and offline models for source-linked reading and comparison."""
import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfWriter

from backend import codex_bridge, parser, provider, store, study


def document(doc_id):
    return {'id': doc_id, 'fingerprint': doc_id, 'title': 'Paper ' + doc_id,
            'filename': doc_id + '.pdf', 'page_count': 2, 'status': 'completed',
            'blocks': [{'id': 'same-block', 'page': 1, 'kind': 'text',
                        'text': 'Our method evaluates 30 papers on the test dataset.',
                        'translation': '我们的方法在测试集上评估了 30 篇论文。', 'status': 'done'}],
            'notes': [{'id': 'note', 'text': 'Do not replace my correction.', 'page': 1}],
            'chats': [{'id': 'old', 'question': 'Old question', 'answer': 'Old answer', 'citations': []}],
            'reading': {'page': 2}, 'research': {}, 'events': [],
            'usage': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'estimated_cost': 0}}


def lookup_result():
    return {'answer': '来源介绍了这项研究。',
            'sources': [{'title': 'Paper', 'url': 'https://example.org/paper', 'claim': '作者与机构署名'}],
            'relationships': [{'subject': 'Author', 'relation': '署名机构', 'object': 'Institute',
                               'source_url': 'https://example.org/paper'}],
            'related': [{'title': 'Related paper', 'year': '2025', 'url': 'https://example.org/paper', 'reason': '共同研究主题'}],
            'limitations': []}


def comparison_response():
    return {'rows': [{'dimension': '核心方法', 'cells': [
        {'document_id': doc_id, 'claim': '在测试集评估 30 篇论文。', 'block_id': 'same-block',
         'page': 999, 'quote': 'evaluates 30 papers on the test dataset.'} for doc_id in ('a', 'b')]}],
        'summary': 'Unsupported claim: Paper a is the best model in the world.', 'limitations': []}


class StudyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='paperloop-study-', dir=os.environ.get('PAPERLOOP_TEST_WORK'))
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        data_patch = patch.object(store, 'DATA', self.data)
        data_patch.start()
        self.addCleanup(data_patch.stop)
        store.init()
        for doc_id in ('a', 'b', 'c'):
            store.insert(document(doc_id))
        active_patch = patch.object(study, 'ACTIVE', set())
        active_patch.start()
        self.addCleanup(active_patch.stop)

    def create_comparison(self, response=None, ids=None):
        with patch.object(provider, 'complete', return_value=json.dumps(response or comparison_response())):
            return study.create_comparison(ids or ['a', 'b'], ['核心方法'])

    def test_lookup_rejects_private_missing_claim_and_dangling_sources(self):
        value = lookup_result()
        value['sources'] += [
            {'title': 'private', 'url': 'http://127.0.0.1:8765/api/settings', 'claim': 'secret'},
            {'title': 'script', 'url': 'javascript:alert(1)', 'claim': 'script'},
            {'title': 'local', 'url': 'file:///C:/secret', 'claim': 'local'},
            {'title': 'no claim', 'url': 'https://example.org/no-claim'},
            value['sources'][0].copy(), None]
        value['relationships'] += [{'subject': 'x', 'relation': 'works for', 'object': 'y', 'source_url': 'https://example.org/missing'}]
        value['related'] += [{'title': 'Fake', 'year': '2026', 'reason': 'invented', 'url': 'https://example.org/missing'}]
        result = study.validate_lookup(value, [{'query': 'paper'}])
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(len(result['relationships']), 1)
        self.assertEqual(len(result['related']), 1)

    def test_lookup_source_cap_cannot_leave_hidden_references(self):
        value = lookup_result()
        value['sources'] = [{'title': str(i), 'url': f'https://example.org/{i}', 'claim': 'Supported'} for i in range(16)]
        value['relationships'][0]['source_url'] = 'https://example.org/15'
        value['related'][0]['url'] = 'https://example.org/15'
        result = study.validate_lookup(value, [{'query': 'paper'}])
        self.assertEqual(len(result['sources']), 15)
        self.assertEqual(result['relationships'], [])
        self.assertEqual(result['related'], [])

    def test_author_github_keeps_personal_and_project_identity_separate(self):
        value = lookup_result()
        value['sources'] += [
            {'title': 'Account', 'url': 'https://github.com/example-author', 'claim': '作者个人简介回链官网'},
            {'title': 'Homepage', 'url': 'https://example.edu/author', 'claim': '作者主页明确链接该 GitHub'},
            {'title': 'Code', 'url': 'https://github.com/project-team/paper-code', 'claim': '官方项目仓库'},
            {'title': 'Team', 'url': 'https://github.com/project-team', 'claim': '论文项目团队组织'}]
        value['github'] = [
            {'url': 'https://github.com/example-author', 'title': 'Example Author', 'kind': 'personal',
             'verification': 'verified', 'detail': '论文署名作者主页直接链接该账号，账号亦回链该主页。',
             'source_urls': ['https://example.edu/author', 'https://github.com/example-author']},
            {'url': 'https://github.com/project-team/paper-code', 'title': 'Official code', 'kind': 'project',
             'verification': 'verified', 'detail': '论文项目页链接该仓库，由项目团队维护，未确认个人所有者。',
             'source_urls': ['https://example.org/paper']},
            {'url': 'https://github.com/project-team', 'title': 'Project team', 'kind': 'organization',
             'verification': 'verified', 'detail': '论文项目团队组织账号，不是作者个人账号。',
             'source_urls': ['https://example.org/paper']}]
        result = study.validate_lookup(value, [{'query': 'author GitHub'}], kind='author')
        self.assertEqual([card['kind'] for card in result['github']], ['personal', 'project', 'organization'])
        self.assertTrue(all(card['verification'] == 'verified' for card in result['github']))
        self.assertEqual(result['github'][0]['source_urls'], ['https://github.com/example-author', 'https://example.edu/author'])
        self.assertFalse(any('尚未找到' in text for text in result['limitations']))

    def test_github_same_name_without_corroboration_is_explicitly_unverified(self):
        value = lookup_result()
        target = 'https://github.com/Example-Author'
        value['sources'] += [
            {'title': 'Same name', 'url': target, 'claim': '搜索返回同名账号'},
            {'title': 'Same page again', 'url': 'https://www.github.com/example-author/?tab=repositories', 'claim': '同一账号页面'}]
        value['github'] = [{'url': target, 'title': 'This definitely belongs to the paper author',
                            'kind': 'personal', 'verification': 'verified', 'detail': 'Only the same name.',
                            'source_urls': ['https://www.github.com/example-author/?tab=repositories', 'https://missing.example/identity']}]
        result = study.validate_lookup(value, [{'query': 'author GitHub'}], kind='author')
        card = result['github'][0]
        self.assertEqual(card['verification'], 'unverified')
        self.assertEqual(card['title'], 'Example-Author')
        self.assertNotIn('definitely belongs', card['detail'])
        self.assertIn('不能仅凭同名', card['detail'])
        self.assertEqual(card['source_urls'], [target])
        self.assertTrue(any('个人 GitHub 账号' in text for text in result['limitations']))

    def test_github_rejects_spoofed_domains_non_accounts_and_unlisted_sources(self):
        value = lookup_result()
        urls = [('https://github.com.evil.example/author', 'personal'),
                ('https://github.com/search', 'personal'),
                ('https://github.com/orgs/research-team', 'personal'),
                ('https://github.com/author/repo/blob/main/README.md', 'project'),
                ('https://github.com/author?tab=repositories', 'personal')]
        value['sources'] += [{'title': 'candidate', 'url': url, 'claim': 'candidate'} for url, kind in urls]
        value['github'] = [{'url': url, 'kind': kind, 'verification': 'verified', 'detail': 'asserted', 'source_urls': ['https://example.org/paper']} for url, kind in urls]
        value['github'].append({'url': 'https://github.com/not-in-sources', 'kind': 'personal', 'verification': 'verified', 'detail': 'asserted'})
        self.assertEqual(study.validate_lookup(value, [{'query': 'q'}], kind='author')['github'], [])
        self.assertTrue(study._github_target('https://github.com/orgs/research-team', 'organization'))

    def test_author_lookup_prompt_requires_github_search_and_missing_result_is_supported(self):
        with patch.object(codex_bridge, 'request', return_value=(json.dumps(lookup_result()), {'web_events': [{'query': 'author GitHub'}]})) as request:
            study.lookup_run('a', 'author', 'Example Author', 'author')
        prompt = request.call_args.args[0][0]['content']
        self.assertIn('必须额外联网搜索作者的 GitHub', prompt)
        self.assertIn('不能', prompt)
        self.assertIn('unverified', prompt)
        result = store.get('a')['lookups']['author']['result']
        self.assertEqual(result['github'], [])
        self.assertTrue(any('尚未找到' in text for text in result['limitations']))
        schema = codex_bridge.response_schema('lookup')['properties']['github']['items']['properties']
        self.assertEqual(schema['kind']['enum'], ['personal', 'organization', 'project'])
        self.assertEqual(schema['verification']['enum'], ['verified', 'unverified'])
        self.assertEqual(schema['source_urls']['items'], {'type': 'string'})

    def test_lookup_requires_actual_web_activity_and_handles_malformed_lists(self):
        with self.assertRaises(provider.ProviderError):
            study.validate_lookup(lookup_result(), [])
        for value in ([], {'answer': None}, {'answer': ''}):
            with self.subTest(value=value), self.assertRaises(provider.ProviderError):
                study.validate_lookup(value, [{'query': 'paper'}])
        result = study.validate_lookup({'answer': 'Unsupported assertion', 'sources': None,
                                        'relationships': [{'source_url': []}], 'related': [{'url': {}}]}, [{}])
        self.assertIn('没有找到可核对', result['answer'])
        self.assertEqual(result['sources'], [])

    def test_lookup_records_activity_and_keeps_paper_content_intact(self):
        before = store.get('a')
        usage = {'input_tokens': 13, 'output_tokens': 7, 'web_events': [{'action': {'type': 'search', 'query': 'paper'}}]}
        with patch.object(codex_bridge, 'request', return_value=(json.dumps(lookup_result()), usage)) as request:
            study.lookup_run('a', 'author', 'Example Author', 'lookup')
        result = store.get('a')
        for key in ('blocks', 'notes', 'chats', 'reading', 'research'):
            self.assertEqual(result[key], before[key])
        self.assertTrue(request.call_args.kwargs['web_search'])
        self.assertEqual(result['lookups']['lookup']['status'], 'completed')
        self.assertEqual(result['lookups']['lookup']['web_activity'], [{'type': 'search', 'query': 'paper'}])
        self.assertEqual(result['usage']['calls'], 1)
        self.assertEqual(result['usage']['input_tokens'], 13)

    def test_lookup_failure_keeps_previous_result_and_records_billed_usage(self):
        previous = lookup_result()
        store.change('a', lambda d: d.update(lookups={'lookup': {'status': 'running', 'result': previous}}))
        with patch.object(codex_bridge, 'request', return_value=('{}', {'input_tokens': 20, 'output_tokens': 3, 'web_events': []})):
            study.lookup_run('a', 'term', 'Test term', 'lookup')
        result = store.get('a')
        self.assertEqual(result['lookups']['lookup']['status'], 'error')
        self.assertEqual(result['lookups']['lookup']['result'], previous)
        self.assertEqual(result['usage']['input_tokens'], 20)
        self.assertFalse(study.ACTIVE)

    def test_lookup_duplicate_click_and_restart_recover_without_data_loss(self):
        with patch.object(codex_bridge, 'status', return_value={'logged_in': True}), patch.object(study.threading, 'Thread') as thread:
            first = study.start_lookup('a', 'term', '  Some   term ')
            second = study.start_lookup('a', 'term', 'some term')
        self.assertEqual(first['key'], second['key'])
        thread.assert_called_once()
        study.recover_interrupted()
        self.assertEqual(store.get('a')['lookups'][first['key']]['status'], 'error')
        self.assertEqual(store.get('a')['blocks'], document('a')['blocks'])

    def test_comparison_quotes_pages_and_preserves_original_documents(self):
        before = store.all_docs()
        value = self.create_comparison()
        self.assertEqual([c['page'] for c in value['rows'][0]['cells']], [1, 1])
        self.assertTrue(all(c['evidence_scope'] == 'PDF 原文' for c in value['rows'][0]['cells']))
        self.assertNotIn('best model', value['summary'])
        self.assertEqual(study.get_comparison(value['id']), value)
        self.assertEqual(store.all_docs(), before)

    def test_missing_claim_and_forged_quote_cannot_be_published_as_verified(self):
        response = comparison_response()
        response['rows'][0]['cells'][0]['claim'] = ''
        response['rows'][0]['cells'][1]['quote'] = 'We obtained a 100 percent success rate.'
        value = self.create_comparison(response)
        for cell in value['rows'][0]['cells']:
            self.assertEqual(cell['evidence_scope'], '未核实')
            self.assertEqual(cell['quote'], '')
            self.assertIn('无法判断', cell['claim'])

    def test_quote_not_sent_to_model_and_single_digit_are_rejected(self):
        store.change('a', lambda d: d['blocks'][0].update(text='method ' + 'x' * 1700 + ' hidden conclusion is perfect.'))
        response = comparison_response()
        response['rows'][0]['cells'][0]['quote'] = 'hidden conclusion is perfect.'
        response['rows'][0]['cells'][1]['quote'] = '3'
        value = self.create_comparison(response)
        self.assertTrue(all(c['page'] == 0 for c in value['rows'][0]['cells']))

    def test_missing_rows_and_malformed_cells_yield_explicit_unknowns(self):
        for rows in ([], [{'dimension': '核心方法', 'cells': None}], [{'dimension': '核心方法', 'cells': [{'document_id': 'a', 'block_id': []}]}]):
            with self.subTest(rows=rows):
                value = self.create_comparison({'rows': rows})
                self.assertEqual(len(value['rows'][0]['cells']), 2)
                self.assertTrue(all(c['page'] == 0 for c in value['rows'][0]['cells']))
        with self.assertRaises(HTTPException):
            study.create_comparison(['a', 'a'], ['核心方法'])
        with self.assertRaises(HTTPException):
            study.create_comparison(['a', 'b'], ['核心方法', '核心方法'])

    def test_followup_uses_same_comparison_history_and_cross_paper_citations(self):
        first = self.create_comparison()
        other = self.create_comparison(ids=['a', 'c'])
        captured = []
        def answer(doc_id, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            captured.append(payload)
            return json.dumps({'answer': '测试集规模相同 [b::same-block]', 'supplement': '', 'citations': [
                {'block_id': 'b::same-block', 'quote': 'evaluates 30 papers on the test dataset.'},
                {'block_id': 'same-block', 'quote': 'evaluates 30 papers on the test dataset.'},
                {'block_id': 'c::same-block', 'quote': 'evaluates 30 papers on the test dataset.'}]})
        with patch.object(provider, 'complete', side_effect=answer):
            chat = study.ask_comparison(first['id'], '它们有什么共同点？')
            again = study.ask_comparison(first['id'], '为什么？')
        self.assertEqual(chat['citations'], [{'document_id': 'b', 'block_id': 'same-block', 'page': 1, 'quote': 'evaluates 30 papers on the test dataset.'}])
        self.assertIn('【原文 1】', chat['answer'])
        self.assertEqual(captured[0]['previous_turns'], [])
        self.assertEqual(captured[1]['previous_turns'], [{'question': chat['question'], 'answer': chat['answer']}])
        self.assertEqual({b['block_id'] for b in captured[0]['evidence']}, {'a::same-block', 'b::same-block'})
        self.assertEqual(study.get_comparison(first['id'])['chats'], [chat, again])
        self.assertEqual(study.get_comparison(other['id'])['chats'], [])
        self.assertEqual(store.get('a')['chats'], document('a')['chats'])

    def test_followup_without_evidence_suppresses_claim_and_model_error_releases_lock(self):
        value = self.create_comparison()
        response = {'answer': 'World-leading claims', 'supplement': 'invented fact', 'citations': []}
        with patch.object(provider, 'complete', return_value=json.dumps(response)):
            chat = study.ask_comparison(value['id'], 'Which wins?')
        self.assertIn('无法给出可靠结论', chat['answer'])
        self.assertEqual(chat['supplement'], '')
        with patch.object(provider, 'complete', side_effect=provider.ProviderError('offline')):
            with self.assertRaises(provider.ProviderError):
                study.ask_comparison(value['id'], 'Retry')
        self.assertFalse(study.COMPARISON_LOCK.locked())
        self.assertEqual(len(study.get_comparison(value['id'])['chats']), 1)

    def test_report_escapes_model_html_and_links_and_deduplicates_crops(self):
        doc = document('a')
        doc['title'] = '<script>alert(1)</script>'
        doc['notes'] = [{'text': '<img src=x onerror=alert(1)>', 'page': 1, 'visual_box': [.1, .2, .8, .7]}]
        doc['chats'] = [{'question': '![track](https://bad.example/x)', 'answer': '<iframe src=x>',
                         'visual_page': 1, 'visual_box': [.1, .2, .8, .7], 'citations': [{'page': 1, 'quote': '<b>quote</b>'}]}]
        doc['lookups'] = {'q': {'status': 'completed', 'query': '<svg>', 'result': lookup_result()}}
        doc['lookups']['q']['result']['sources'].append({'title': 'unsafe', 'url': 'javascript:alert(1)'})
        before = copy.deepcopy(doc)
        with patch.object(parser, 'png_bytes', return_value=b'png bytes') as render:
            output = study.reading_report_html(doc)
        render.assert_called_once_with(self.data / 'a.pdf', 1, [.1, .2, .8, .7])
        self.assertIn('data:image/png;base64,' + base64.b64encode(b'png bytes').decode(), output)
        self.assertIn('href="#evidence-image-1"', output)
        self.assertIn('&lt;script&gt;', output)
        self.assertNotIn('<script>', output)
        self.assertNotIn('<iframe', output)
        self.assertNotIn('href="javascript:', output)
        self.assertNotIn('<img src=x', output)
        self.assertIn('href="https://example.org/paper"', output)
        self.assertEqual(doc, before)
        markdown = study.reading_report(doc)
        self.assertNotIn('<script>', markdown)
        self.assertNotIn('![track]', markdown)

    def test_report_renders_actual_pdf_crop_without_external_resource(self):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.write(self.data / 'a.pdf')
        doc = document('a')
        doc['notes'][0]['visual_box'] = [.1, .1, .5, .5]
        output = study.reading_report_html(doc)
        self.assertIn('src="data:image/png;base64,iVBOR', output)
        self.assertNotIn('src="http', output)

    def test_report_invalid_or_unreadable_crop_keeps_text_evidence(self):
        doc = document('a')
        doc['notes'][0]['visual_box'] = [0, 0, float('nan'), 1]
        with patch.object(parser, 'png_bytes') as render:
            output = study.reading_report_html(doc)
        render.assert_not_called()
        self.assertIn('坐标无效', output)
        doc['notes'][0]['visual_box'] = [0, 0, 1, 1]
        with patch.object(parser, 'png_bytes', side_effect=OSError('missing PDF')):
            output = study.reading_report_html(doc)
        self.assertIn('图像暂无法读取', output)
        self.assertIn(doc['notes'][0]['text'], output)

    def test_note_report_preserves_quote_color_and_multiline_highlight_crop(self):
        doc = document('a')
        boxes = [[.15, .2, .85, .24], [.1, .25, .5, .3]]
        doc['notes'][0].update(quote='Selected <script>excerpt</script>\nSecond line',
                                color='blue', boxes=boxes)
        original = copy.deepcopy(doc)
        with patch.object(parser, 'png_bytes', return_value=b'png') as render:
            output = study.reading_report_html(doc)
        render.assert_called_once_with(self.data / 'a.pdf', 1, [.1, .2, .85, .3])
        self.assertIn('note-quote note-blue', output)
        self.assertIn('选中摘录 · 蓝色', output)
        self.assertIn('Selected &lt;script&gt;excerpt&lt;/script&gt;\nSecond line', output)
        self.assertIn('文字高亮：2 个区域', output)
        self.assertNotIn('<script>', output)
        self.assertEqual(doc, original)
        markdown = study.reading_report(doc)
        self.assertIn('> Selected &lt;script&gt;excerpt&lt;/script&gt;\n> Second line', markdown)
        self.assertIn('高亮颜色：蓝色', markdown)
        self.assertIn('逐行区域：' + json.dumps(boxes), markdown)

    def test_note_report_prefers_explicit_visual_crop_and_rejects_invalid_highlights(self):
        doc = document('a')
        doc['notes'][0].update(quote='Selected excerpt', color='pink', boxes=[[.1, .2, .8, .3]],
                                visual_box=[.2, .2, .5, .5])
        with patch.object(parser, 'png_bytes', return_value=b'png') as render:
            output = study.reading_report_html(doc)
        render.assert_called_once_with(self.data / 'a.pdf', 1, [.2, .2, .5, .5])
        self.assertIn('note-quote note-pink', output)
        doc['notes'][0].pop('visual_box')
        doc['notes'][0].update(color='blue\" onclick=\"alert(1)', boxes=[[0, 0, float('nan'), 1]])
        with patch.object(parser, 'png_bytes') as render:
            output = study.reading_report_html(doc)
        render.assert_not_called()
        self.assertIn('note-quote note-yellow', output)
        self.assertIn('文字高亮坐标无效', output)
        self.assertNotIn('onclick=', output)
        self.assertIn('Selected excerpt', output)

    def test_comparison_markdown_contains_saved_followups_and_safe_cells(self):
        value = self.create_comparison()
        value['papers'][0]['title'] = '<img src=x> | extra\nrow'
        value['chats'] = [{'question': '![link](https://example.org/x)', 'answer': '<script>bad</script>',
                           'citations': [{'document_id': 'a', 'block_id': 'same-block', 'page': 1, 'quote': 'source'}]}]
        output = study.comparison_markdown(value)
        self.assertIn('比较追问', output)
        self.assertNotIn('<script>', output)
        self.assertNotIn('<img', output)
        self.assertIn('／ extra row', output)
        self.assertNotIn('![link]', output)


if __name__ == '__main__':
    unittest.main()
