"""Offline tests for source provenance, resumable research and paragraph follow-ups."""
import copy
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import app as api
from backend import codex_bridge, provider, research, store
from tests.test_worker_regressions import block, document
from tests import test_codex_bridge as bridge_tests


def report():
    return {'summary':'文档解析研究。',
        'publication':{'status':'预印本','venue':'arXiv','year':'2025','detail':'上传版为预印本。','source_ids':['s1']},
        'authors':[{'name':'Example Author','affiliation':'署名单位','role':'作者','detail':'信息来自论文。','source_ids':['s1']}],
        'significance':[{'title':'结构识别','detail':'作者报告改进。','kind':'paper_claim','source_ids':['s1']}],
        'sources':[{'id':'s1','title':'原文','url':'https://arxiv.org/abs/2506.05218'}], 'limitations':[]}


class ResearchAndContext(unittest.TestCase):
    def setUp(self):
        self.doc=document()
        self.doc.update(status='completed',usage={'calls':0,'input_tokens':0,'output_tokens':0},
                        research={'status':'running','report':{'summary':'previous report'}})
        def change(doc_id,operation):
            value=copy.deepcopy(self.doc)
            operation(value)
            self.doc=value
            return copy.deepcopy(value)
        replacements=[patch.object(store,'get',side_effect=lambda _:copy.deepcopy(self.doc)),
            patch.object(store,'change',side_effect=change),
            patch.object(store,'all_docs',side_effect=lambda:[copy.deepcopy(self.doc)]),
            patch.object(store,'settings',return_value=store.DEFAULT_SETTINGS.copy()),
            patch.object(research,'ACTIVE',set()),patch.object(provider,'configured',return_value=True)]
        for item in replacements:
            item.start()
            self.addCleanup(item.stop)

    def test_actual_web_activity_required_and_refresh_error_keeps_last_report(self):
        with patch.object(codex_bridge,'request',return_value=(json.dumps(report()),{'web_events':[]})):
            research.run(self.doc['id'])
        self.assertEqual(self.doc['research']['status'],'error')
        self.assertIn('没有完成联网搜索',self.doc['research']['error'])
        self.assertEqual(self.doc['research']['report']['summary'],'previous report')
        self.assertEqual(self.doc['status'],'completed')

    def test_success_records_sources_usage_queries_and_preserves_translation(self):
        original=copy.deepcopy(self.doc['blocks'])
        usage={'input_tokens':123,'output_tokens':45,'web_events':[{'query':'paper publication'}]}
        with patch.object(codex_bridge,'request',return_value=(json.dumps(report()),usage)) as request:
            research.run(self.doc['id'])
        self.assertTrue(request.call_args.kwargs['web_search'])
        self.assertEqual(self.doc['research']['status'],'completed')
        self.assertEqual(self.doc['research']['queries'],['paper publication'])
        self.assertEqual(self.doc['usage'],{'calls':1,'input_tokens':123,'output_tokens':45})
        self.assertEqual(self.doc['blocks'],original)

    def test_broken_or_private_sources_rejected(self):
        for url in ('javascript:alert(1)','http://127.0.0.1:8765/api/settings','https://localhost/',
                    'file:///C:/secret','http://[::1]/','https://user:password@example.org/'):
            value=report()
            value['sources'][0]['url']=url
            with self.subTest(url=url),self.assertRaises(provider.ProviderError):
                research.validate_report(value,[{'query':'q'}])
        value=report()
        value['publication']['source_ids']=['missing']
        with self.assertRaises(provider.ProviderError):
            research.validate_report(value,[{'query':'q'}])

    def test_unsourced_identity_and_publication_not_presented_as_fact(self):
        value=report()
        value['publication']['source_ids']=[]
        value['authors'][0]['source_ids']=[]
        result=research.validate_report(value,[{'query':'q'}])
        self.assertEqual(result['publication']['status'],'未核实')
        self.assertEqual(result['authors'][0]['affiliation'],'待核实')

    def test_restart_and_duplicate_click_do_not_leave_permanent_running(self):
        research.recover_interrupted()
        self.assertEqual(self.doc['research']['status'],'error')
        with patch.object(codex_bridge,'status',return_value={'logged_in':True}),patch.object(research.threading,'Thread') as thread:
            self.assertEqual(research.start(self.doc['id']),{'status':'running'})
            self.assertEqual(research.start(self.doc['id']),{'status':'running'})
            thread.assert_called_once()
        self.assertEqual(self.doc['research']['report']['summary'],'previous report')

    def test_followup_receives_only_same_paragraph_history_and_explicit_focus(self):
        self.doc['blocks']=[block('first'),block('second',order=1)]
        self.doc['chats']=[{'question':'old paper','answer':'paper'},
            {'question':'explain first','answer':'first answer','context_key':'selection:first'},
            {'question':'other question','answer':'unrelated','context_key':'selection:second'}]
        captured=[]
        def complete(doc_id,messages,**kwargs):
            captured.append(json.loads(messages[-1]['content']))
            return json.dumps({'answer':'继续解释。','citations':[{'block_id':'first','quote':self.doc['blocks'][0]['text']}],'supplement':''})
        with patch.object(provider,'complete',side_effect=complete):
            chat=api.ask(self.doc['id'],api.Question(question='为什么？',scope='selection',block_id='first',page=99))
        self.assertEqual(captured[0]['previous_turns'],[{'question':'explain first','answer':'first answer'}])
        self.assertEqual(captured[0]['focus_block_id'],'first')
        self.assertEqual(chat['context_key'],'selection:first')
        self.assertEqual(chat['page'],1)
        with self.assertRaises(HTTPException):
            api.question_context(self.doc,api.Question(question='q',scope='page',page=99))


def test_live_mode(self):
    def run(args,**kwargs):
        self.assertIn('web_search="live"',args)
        self.assertIn('features.shell_tool=false',args)
        self.assertNotIn('Do not use tools',kwargs['input'])
        Path(args[args.index('-o')+1]).write_text(json.dumps(report()),encoding='utf-8')
        event={'type':'item.completed','item':{'type':'web_search','query':'original title publication'}}
        return subprocess.CompletedProcess(args,0,stdout=json.dumps(event),stderr='')
    with patch.object(codex_bridge,'status',return_value={'logged_in':True}),patch.object(codex_bridge.subprocess,'run',side_effect=run):
        content,usage=codex_bridge.request([{'role':'user','content':json.dumps({'task':'research'})}],web_search=True)
    self.assertEqual(json.loads(content),report())
    self.assertEqual(usage['web_events'][0]['query'],'original title publication')


# unittest fixture reuse without running inherited tests twice.
class WebBridgeMode(unittest.TestCase):
    setUp=bridge_tests.CodexBridgeTests.setUp
    test_live_mode=test_live_mode


if __name__=='__main__':
    unittest.main()
