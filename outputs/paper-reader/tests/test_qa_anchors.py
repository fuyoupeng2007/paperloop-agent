"""Conversations for paper regions stay attached to the selected evidence."""
import copy
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import app as api
from backend import layout_objects, parser, provider, store
from tests.test_worker_regressions import block, document


class QAAnchors(unittest.TestCase):
    def setUp(self):
        self.doc = document()
        self.doc['blocks'] = [block('first'), block('caption', order=1, kind='caption', text='Figure 1. Comparison.')]
        self.model_inputs = []

        def change(doc_id, operation):
            candidate = copy.deepcopy(self.doc)
            operation(candidate)
            self.doc = candidate
            return copy.deepcopy(candidate)

        def complete(doc_id, messages, **kwargs):
            payload = messages[-1]['content']
            if isinstance(payload, list):
                payload = payload[0]['text']
            self.model_inputs.append(json.loads(payload))
            return json.dumps({'answer': '有证据的说明。', 'citations': [], 'supplement': ''})

        for item in (
            patch.object(store, 'get', side_effect=lambda _: copy.deepcopy(self.doc)),
            patch.object(store, 'change', side_effect=change),
            patch.object(store, 'settings', return_value=store.DEFAULT_SETTINGS.copy()),
            patch.object(provider, 'configured', return_value=True),
            patch.object(provider, 'complete', side_effect=complete),
            patch.object(parser, 'png_bytes', return_value=b'png'),
            patch.object(layout_objects, 'get_layout', return_value={'objects': [
                {'id': 'figure-p1-abc', 'page': 1, 'bbox': [.1, .2, .4, .5],
                 'label': '图 1', 'caption': 'Figure 1. Comparison.'},
                {'id': 'figure-p1-def', 'page': 1, 'bbox': [.5, .2, .8, .5],
                 'label': '图 2', 'caption': 'Figure 2. Comparison.'},
            ]}),
        ):
            item.start()
            self.addCleanup(item.stop)

    def test_two_crops_on_one_page_keep_separate_followups_and_geometry(self):
        first_box = [.1, .2, .4, .5]
        second_box = [.5, .2, .8, .5]
        first = api.ask(self.doc['id'], api.Question(question='图一是什么？', mode='visual', page=1,
                                                    box=first_box, anchor_id='figure-p1-abc'))
        second = api.ask(self.doc['id'], api.Question(question='图二是什么？', mode='visual', page=1,
                                                     box=second_box, anchor_id='figure-p1-def'))
        followup = api.ask(self.doc['id'], api.Question(question='刚才的图还有什么？', mode='visual',
                                                       page=1, anchor_id='figure-p1-abc'))
        self.assertEqual(first['context_key'], 'object:figure-p1-abc')
        self.assertEqual(second['context_key'], 'object:figure-p1-def')
        self.assertEqual(first['visual_box'], first_box)
        self.assertEqual(followup['visual_box'], first_box)
        self.assertEqual(self.model_inputs[2]['focus_region']['box'], first_box)
        self.assertEqual(self.model_inputs[2]['focus_region']['label'], '图 1')
        self.assertEqual(self.model_inputs[2]['previous_turns'],
                         [{'question': '图一是什么？', 'answer': '有证据的说明。'}])
        self.assertEqual(len(self.doc['chats']), 3)

    def test_manual_crops_use_coordinates_instead_of_shared_page_history(self):
        one = api.ask(self.doc['id'], api.Question(question='第一处', mode='visual', page=1,
                                                  box=[.1, .1, .3, .3]))
        two = api.ask(self.doc['id'], api.Question(question='第二处', mode='visual', page=1,
                                                  box=[.4, .4, .6, .6]))
        self.assertNotEqual(one['context_key'], two['context_key'])
        self.assertTrue(one['context_key'].startswith('region:1:'))
        self.assertEqual(self.model_inputs[1]['previous_turns'], [])

    def test_client_manual_crop_anchor_reuses_region_but_rejects_changed_geometry(self):
        box = [.11, .21, .31, .41]
        first = api.ask(self.doc['id'], api.Question(question='框选内容', mode='visual', page=1,
            box=box, anchor_id='crop-1a2b3c4d'))
        followup = api.ask(self.doc['id'], api.Question(question='继续解释', mode='visual', page=1,
            anchor_id='crop-1a2b3c4d'))
        self.assertEqual(first['context_key'], 'region:1:crop-1a2b3c4d')
        self.assertEqual(followup['visual_box'], box)
        self.assertEqual(len(self.model_inputs[1]['previous_turns']), 1)
        with self.assertRaises(HTTPException):
            api.ask(self.doc['id'], api.Question(question='另一块区域', mode='visual', page=1,
                box=[.5, .5, .9, .9], anchor_id='crop-1a2b3c4d'))

    def test_figure_uses_canonical_bbox_and_rejects_wrong_page(self):
        result = api.ask(self.doc['id'], api.Question(question='图中的关系', mode='visual', page=1,
            box=[.2, .3, .3, .4], anchor_id='figure-p1-abc'))
        self.assertEqual(result['visual_box'], [.1, .2, .4, .5])
        self.doc['page_count'] = 2
        with self.assertRaises(HTTPException):
            api.ask(self.doc['id'], api.Question(question='错页', mode='visual', page=2,
                box=[.1, .2, .4, .5], anchor_id='figure-p1-abc'))

    def test_text_anchor_scopes_selected_quote_but_keeps_legacy_paragraph(self):
        legacy = api.question_context(self.doc, api.Question(question='段落', scope='selection', block_id='first'))
        self.assertEqual(legacy, 'selection:first')
        a = api.question_context(self.doc, api.Question(question='解释A', scope='selection',
                                                        block_id='first', selection='reading method', anchor_id='text-abcd'))
        b = api.question_context(self.doc, api.Question(question='解释B', scope='selection',
                                                        block_id='first', selection='comprehension', anchor_id='text-efgh'))
        self.assertEqual(a, 'selection:first:text-abcd')
        self.assertEqual(b, 'selection:first:text-efgh')
        self.assertNotEqual(a, b)

    def test_anchored_text_followup_recovers_the_original_selection(self):
        original = api.ask(self.doc['id'], api.Question(question='解释这个词', scope='selection',
            block_id='first', selection='reading method', anchor_id='text-abcd'))
        followup = api.ask(self.doc['id'], api.Question(question='为什么？', scope='selection',
            block_id='first', anchor_id='text-abcd'))
        self.assertEqual(original['context_key'], followup['context_key'])
        self.assertEqual(followup['selected_text'], 'reading method')
        self.assertEqual(self.model_inputs[1]['selected_text'], 'reading method')
        self.assertEqual(len(self.model_inputs[1]['previous_turns']), 1)

    def test_invalid_anchor_and_box_rejected_before_model_call(self):
        for value in (
            api.Question(question='x', mode='visual', page=1, box=[.1, .2, .3, .4], anchor_id='bad/route'),
            api.Question(question='x', mode='visual', page=1, box=[.1, .2, .1, .4]),
            api.Question(question='x', mode='visual', page=1, anchor_id='figure-p1-new'),
        ):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                api.ask(self.doc['id'], value)
        self.assertEqual(self.model_inputs, [])


if __name__ == '__main__':
    unittest.main()
