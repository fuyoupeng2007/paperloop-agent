"""Local figure/table evidence is additive, geometrically sound and persistent.

Every database used here lives in a temporary workspace directory. No app
lifespan is started, and OCR/model output is supplied locally by the tests.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from PIL import Image

from backend import app as api
from backend import layout_objects, parser, provider, reading_details, store
from tests.test_worker_regressions import block, document


def figure(object_id='figure-p1-example', page=1):
    return {'id': object_id, 'kind': 'figure', 'page': page,
            'bbox': [.2, .3, .8, .7], 'label': '图 1',
            'caption': 'Figure 1. Example chart.'}


def details(obj):
    return {'object_id': obj['id'], 'page': obj['page'], 'warning': '',
            'tokens': [{'id': 'native-0', 'text': 'Accuracy',
                        'bbox': [.25, .35, .4, .4], 'source': 'pdf-text'}],
            'marks': [{'id': obj['id'] + ':bar:1', 'kind': 'bar',
                       'bbox': [.3, .4, .35, .6], 'label': '柱子候选 1',
                       'value': '', 'unit': '', 'verification': 'candidate'}],
            'cells': [], 'header_groups': []}


def fake_pdf(page):
    pdf = MagicMock()
    pdf.__enter__.return_value = SimpleNamespace(pages=[page])
    return pdf


def corrected_table():
    rows = [
        ['M⁶Doc [6]', '6', '✓', '', '', '', '', '✓', '✓'],
        ['CDLA [18]', '1', '✓', '', '', '', '', '', '✓'],
        ['D4LA [8]', '5', '✓', '', '', '', '', '✓', ''],
        ['DocLayNet [37]', '2', '✓', '', '', '', '', '✓', '✓'],
        ['Unimernet [42]', '-', '', '', '✓', '', '', '', ''],
        ['TexTeller [31]', '-', '', '', '✓', '', '', '', ''],
        ['FinTabNet [51]', '-', '', '', '', '✓', '', '', ''],
        ['PubTabNet [52]', '-', '', '', '', '✓', '', '', ''],
        ['DocGenome [46]', '1', '✓', '✓', '✓', '✓', '✓', '✓', ''],
        ['MonkeyDoc', '>10', '✓', '✓', '✓', '✓', '✓', '✓', '✓'],
    ]
    source = {'id': 'p5-97695a748e-3', 'kind': 'table', 'page': 5,
              'bbox': [.176, .468, .817, .716], 'cells': rows,
              'text': 'Previously verified table', 'translation': '已有人工核对表格译文'}
    doc = {'id': 'synthetic-table', 'fingerprint': reading_details.MONKEY_V1, 'blocks': [source]}
    obj = {'id': 'table-p5-example', 'block_id': source['id'], 'kind': 'table',
           'page': 5, 'bbox': source['bbox'], 'caption': 'Table 1. Datasets.', 'label': '表 1'}
    return doc, obj


class ReadingDetailsGeometry(unittest.TestCase):
    def test_native_words_belong_to_object_and_use_page_coordinates(self):
        page = SimpleNamespace(width=1000, height=2000, extract_words=lambda: [
            {'x0': 250, 'top': 700, 'x1': 400, 'bottom': 800, 'text': 'Accuracy'},
            {'x0': 20, 'top': 50, 'x1': 180, 'bottom': 90, 'text': 'Page heading'},
            {'x0': 300, 'top': 1510, 'x1': 600, 'bottom': 1550, 'text': 'Caption'},
        ])
        with patch.object(reading_details.pdfplumber, 'open', return_value=fake_pdf(page)):
            words = reading_details._native_words(Path('unused.pdf'), figure())
        self.assertEqual([w['text'] for w in words], ['Accuracy'])
        self.assertEqual(words[0]['bbox'], [.25, .35, .4, .4])

    def test_native_word_anchor_ids_do_not_depend_on_extraction_order(self):
        words = [{'x0': 250, 'top': 700, 'x1': 400, 'bottom': 800, 'text': 'Accuracy'},
                 {'x0': 450, 'top': 700, 'x1': 600, 'bottom': 800, 'text': 'Speed'}]
        page = SimpleNamespace(width=1000, height=2000, extract_words=lambda: words)
        with patch.object(reading_details.pdfplumber, 'open', return_value=fake_pdf(page)):
            first = reading_details._native_words(Path('unused.pdf'), figure())
            words.reverse()
            second = reading_details._native_words(Path('unused.pdf'), figure())
        self.assertEqual({w['text']: w['id'] for w in first}, {w['text']: w['id'] for w in second})

    def test_local_ocr_maps_crop_pixels_to_page_and_discards_low_confidence(self):
        result = SimpleNamespace(
            boxes=[[[60, 40], [240, 40], [240, 200], [60, 200]],
                   [[0, 0], [12, 0], [12, 12], [0, 12]]],
            txts=['  Model A  ', 'noise'], scores=[.91, .2])
        engine = MagicMock(return_value=result)
        with patch.dict('sys.modules', {'rapidocr': SimpleNamespace(RapidOCR=lambda: engine)}), \
                patch.object(parser, '_ocr', engine), \
                patch.object(parser, 'page_image', return_value=Image.new('RGB', (600, 400))):
            tokens, warning = reading_details._ocr_words(Path('unused.pdf'), figure())
        self.assertEqual(warning, '')
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['text'], 'Model A')
        self.assertEqual(tokens[0]['bbox'], [.26, .34, .44, .5])
        self.assertEqual(tokens[0]['source'], 'local-ocr')

    def test_same_shaped_table_elsewhere_on_page_is_not_relabelled(self):
        doc = {'blocks': [{'id': 'table-block', 'cells': [['Name', 'Score'], ['A', '42']]}]}
        obj = {'id': 'table-p1-example', 'block_id': 'table-block',
               'kind': 'table', 'page': 1, 'bbox': [.1, .1, .4, .4]}
        other = SimpleNamespace(bbox=(600, 600, 900, 900), columns=[None, None], rows=[
            SimpleNamespace(cells=[(600, 600, 750, 750), (750, 600, 900, 750)]),
            SimpleNamespace(cells=[(600, 750, 750, 900), (750, 750, 900, 900)]),
        ])
        page = SimpleNamespace(width=1000, height=1000, find_tables=lambda: [other])
        with patch.object(reading_details.pdfplumber, 'open', return_value=fake_pdf(page)):
            cells, groups, warning = reading_details._table_cells(doc, obj, Path('unused.pdf'))
        self.assertEqual(cells, [])
        self.assertTrue(warning)

    def test_corrected_table_keeps_blank_cells_merged_headers_and_group_gaps(self):
        doc, obj = corrected_table()
        before = copy.deepcopy(doc)
        cells, groups, warning = reading_details._table_cells(doc, obj, Path('unused.pdf'))
        body = [c for c in cells if not c.get('header')]
        headers = [c for c in cells if c.get('header')]
        self.assertEqual(len(body), 90)
        self.assertEqual(len(headers), 11)
        self.assertEqual(doc, before)
        self.assertEqual(warning, '')
        self.assertEqual(groups, [{'label': 'Supporting Tasks', 'columns': [2, 3, 4, 5, 6]},
                                  {'label': 'Language', 'columns': [7, 8]}])
        for r, row in enumerate(before['blocks'][0]['cells']):
            self.assertEqual([c['value'] for c in body if c['row'] == r], row)
        # These positions lie in the intervening group-label bands, not data rows.
        for y in (465 / 792, 500 / 792, 536 / 792):
            self.assertFalse(any(c['bbox'][1] <= y <= c['bbox'][3] for c in body))
        # Check real row centers independently of the implementation's intervals.
        row_centers = (421, 431, 441, 451, 477, 488, 513, 524, 550, 560)
        for r, y in enumerate(row_centers):
            self.assertTrue(all(c['bbox'][1] < y / 792 < c['bbox'][3]
                                for c in body if c['row'] == r))
        self.assertEqual(next(c for c in body if c['row'] == 2 and c['column'] == 8)['value'], '')
        self.assertEqual(next(c for c in body if c['row'] == 8 and c['column'] == 7)['value'], '✓')
        self.assertEqual(next(c for c in headers if c['value'] == 'Supporting Tasks')['col_span'], 5)
        self.assertEqual(next(c for c in headers if c['value'] == 'Language')['col_span'], 2)
        self.assertEqual(next(c for c in headers if c['value'] == 'Dataset')['row_span'], 2)

    def test_chart_verified_values_require_exact_pdf_and_preserve_metric_units(self):
        obj = figure()

        def mark(x):
            center = obj['bbox'][0] + x * (obj['bbox'][2] - obj['bbox'][0])
            return {'id': str(x), 'bbox': [center - .002, .4, center + .002, .6],
                    'verification': 'candidate', 'value': '', 'unit': '', 'label': 'Candidate'}

        # Last series of the first group must not be confused with group 2.
        marks = [mark(.1808), mark(.835)]
        unrelated = {'fingerprint': 'other-version', 'title': 'MonkeyOCR'}
        self.assertEqual(reading_details._monkey_chart_labels(unrelated, obj, copy.deepcopy(marks)), marks)
        checked = reading_details._monkey_chart_labels({'fingerprint': reading_details.MONKEY_V1}, obj, marks)
        self.assertEqual(checked[0]['category'], 'Formula (EN)')
        self.assertEqual(checked[0]['series'], 'InternVL3-8B')
        self.assertEqual(checked[0]['value'], '78.3')
        self.assertEqual(checked[0]['metric'], 'CDM')
        self.assertEqual(checked[1]['metric'], 'Pages/s')
        self.assertEqual(checked[1]['value'], '0.84')
        self.assertEqual(checked[1]['unit'], 'Pages/s')
        self.assertNotEqual(checked[0]['unit'], checked[1]['unit'])
        self.assertTrue(all(c['value_source'] for c in checked))

    def test_table_reference_geometry_is_not_applied_to_another_pdf(self):
        doc, obj = corrected_table()
        doc['fingerprint'] = 'different-pdf-with-same-block-id'
        pdf = MagicMock()
        pdf.__enter__.return_value = SimpleNamespace(pages=[None] * 4 + [
            SimpleNamespace(width=612, height=792, find_tables=lambda: [])])
        with patch.object(reading_details.pdfplumber, 'open', return_value=pdf):
            cells, groups, warning = reading_details._table_cells(doc, obj, Path('unused.pdf'))
        self.assertEqual(cells, [])
        self.assertTrue(warning)


class ReadingDetailsPersistence(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[3] / 'work'
        work.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='reading-details-test-', dir=work)
        self.assertEqual(Path(temporary.name).resolve().parent, work.resolve())
        self.addCleanup(temporary.cleanup)
        data_patch = patch.object(store, 'DATA', Path(temporary.name))
        data_patch.start()
        self.addCleanup(data_patch.stop)
        store.init()
        self.doc = document()
        self.doc.update(fingerprint='synthetic-fingerprint', page_count=2,
                        status='completed', title='Local synthetic chart')
        self.doc['blocks'] = [block('source', translation='已有的人工校正译文', status='done'),
                              block('table', page=2, cells=[['A', ''], ['B', '✓']],
                                    translation='已有表格译文', kind='table')]
        self.doc['notes'] = [{'id': 'old-note', 'text': '以前的笔记', 'page': 1}]
        self.doc['chats'] = [{'id': 'old-chat', 'question': '以前的问题', 'answer': '以前的回答'}]
        store.insert(self.doc)
        self.obj = figure()

    def test_token_ids_are_isolated_per_parent_and_corrections_survive_reselection(self):
        second = figure('figure-p1-second')
        with patch.object(reading_details, 'get_details', side_effect=lambda doc, obj: details(obj)):
            first = reading_details.save_anchor(self.doc, self.obj, 'token', 'native-0')
            other = reading_details.save_anchor(self.doc, second, 'token', 'native-0')
            repeat = reading_details.save_anchor(self.doc, self.obj, 'token', 'native-0')
            bar_id = details(self.obj)['marks'][0]['id']
            corrected = reading_details.save_anchor(self.doc, self.obj, 'bar', bar_id,
                correction={'series': 'Verified model', 'value': '42', 'unit': '%'})
            retained = reading_details.save_anchor(self.doc, self.obj, 'bar', bar_id)
        self.assertNotEqual(first['id'], other['id'])
        self.assertEqual(first['id'], repeat['id'])
        self.assertEqual(reading_details.get_anchor(self.doc['id'], first['id'])['parent_id'], self.obj['id'])
        self.assertEqual(reading_details.get_anchor(self.doc['id'], other['id'])['parent_id'], second['id'])
        self.assertEqual(corrected['id'], retained['id'])
        self.assertEqual(retained['correction'], corrected['correction'])
        self.assertEqual(reading_details.get_anchor(self.doc['id'], corrected['id'])['correction'],
                         corrected['correction'])
        self.assertEqual(store.get(self.doc['id']), self.doc)

    def test_details_cache_and_refresh_leave_existing_document_exactly_unchanged(self):
        native = [{'id': 'native-0', 'text': 'Model A', 'bbox': [.25, .35, .4, .4]}]
        ocr = [{'id': 'ocr-0', 'text': 'Model A', 'bbox': [.25, .35, .4, .4]},
               {'id': 'ocr-1', 'text': 'Value', 'bbox': [.5, .5, .6, .55]}]
        with patch.object(reading_details, '_native_words', return_value=native) as extraction, \
                patch.object(reading_details, '_ocr_words', return_value=(ocr, '')), \
                patch.object(reading_details, '_chart_marks', return_value=[]):
            result = reading_details.get_details(self.doc, self.obj)
            self.assertEqual([token['text'] for token in result['tokens']], ['Model A', 'Value'])
            cached = reading_details.get_details(self.doc, self.obj)
            self.assertEqual(cached, result)
            self.assertEqual(extraction.call_count, 1)
            reading_details.get_details(self.doc, self.obj, refresh=True)
            self.assertEqual(extraction.call_count, 2)
        self.assertEqual(store.get(self.doc['id']), self.doc)

    def test_old_geometry_cache_is_rebuilt_without_removing_saved_corrections(self):
        with patch.object(reading_details, 'get_details', return_value=details(self.obj)):
            anchor = reading_details.save_anchor(self.doc, self.obj, 'bar', self.obj['id'] + ':bar:1',
                                                 correction={'series': 'Checked model'})
        with store.connect() as db:
            db.execute('INSERT INTO object_details VALUES(?,?,?)',
                       (self.doc['id'], self.obj['id'], json.dumps({'version': -1, 'tokens': ['stale']})))
        with patch.object(reading_details, '_native_words', return_value=[]), \
                patch.object(reading_details, '_ocr_words', return_value=([], '')), \
                patch.object(reading_details, '_chart_marks', return_value=[]):
            refreshed = reading_details.get_details(self.doc, self.obj)
        self.assertEqual(refreshed['tokens'], [])
        self.assertEqual(refreshed['version'], reading_details.DETAIL_VERSION)
        self.assertEqual(reading_details.get_anchor(self.doc['id'], anchor['id'])['correction'],
                         {'series': 'Checked model'})
        self.assertEqual(store.get(self.doc['id']), self.doc)

    def test_row_and_column_anchors_are_stable_across_seed_cells(self):
        doc, obj = corrected_table()
        cells, groups, warning = reading_details._table_cells(doc, obj, Path('unused.pdf'))
        result = {'marks': [], 'tokens': [], 'cells': cells, 'header_groups': groups}
        with patch.object(reading_details, 'get_details', return_value=result):
            first = reading_details.save_anchor(doc, obj, 'row', obj['id'] + ':cell:8:0')
            second = reading_details.save_anchor(doc, obj, 'row', obj['id'] + ':cell:8:8')
            column = reading_details.save_anchor(doc, obj, 'column', obj['id'] + ':cell:0:8')
            same_column = reading_details.save_anchor(doc, obj, 'column', obj['id'] + ':cell:9:8')
            cell = reading_details.save_anchor(doc, obj, 'cell', obj['id'] + ':cell:8:0')
            with self.assertRaises(ValueError):
                reading_details.save_anchor(doc, obj, 'row', obj['id'] + ':header:0')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(column['id'], same_column['id'])
        self.assertNotEqual(first['id'], cell['id'])
        self.assertEqual(len(first['data']['members']), 9)
        self.assertEqual(len(column['data']['members']), 10)
        self.assertEqual(first['label'], '整行：DocGenome [46]')
        boxes = [member['bbox'] for member in first['data']['members']]
        self.assertEqual(first['bbox'], [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                        max(b[2] for b in boxes), max(b[3] for b in boxes)])
        self.assertEqual(store.get(self.doc['id']), self.doc)

    def test_bar_pair_and_text_range_have_order_independent_anchors(self):
        result = details(self.obj)
        result['marks'].append({'id': self.obj['id'] + ':bar:2', 'label': '柱子候选 2',
                                'bbox': [.4, .45, .45, .6], 'value': '', 'unit': ''})
        result['tokens'].extend([
            {'id': 'native-1', 'text': 'and', 'bbox': [.41, .35, .45, .4]},
            {'id': 'native-2', 'text': 'Speed', 'bbox': [.46, .35, .55, .4]},
        ])
        bar_ids = [m['id'] for m in result['marks']]
        with patch.object(reading_details, 'get_details', return_value=result):
            first = reading_details.save_anchor(self.doc, self.obj, 'bar_pair', '|'.join(bar_ids))
            reverse = reading_details.save_anchor(self.doc, self.obj, 'bar_pair', '|'.join(reversed(bar_ids)))
            words = reading_details.save_anchor(self.doc, self.obj, 'token_range', 'native-2|native-0')
            words_reverse = reading_details.save_anchor(self.doc, self.obj, 'token_range', 'native-0|native-2')
            with self.assertRaises(ValueError):
                reading_details.save_anchor(self.doc, self.obj, 'bar_pair', '|'.join([bar_ids[0]] * 2))
        self.assertEqual(first['id'], reverse['id'])
        self.assertEqual(len(first['data']['members']), 2)
        self.assertEqual(words['id'], words_reverse['id'])
        self.assertEqual(words['data']['text'], 'Accuracy and Speed')
        self.assertEqual(words['bbox'], [.25, .35, .55, .4])

    def test_local_anchor_qa_recovers_geometry_correction_and_same_context_only(self):
        captured = []

        def answer(doc_id, messages, **kwargs):
            captured.append(json.loads(messages[-1]['content'][0]['text']))
            return json.dumps({'answer': '这根柱子的局部解释。', 'citations': [], 'supplement': ''})

        with patch.object(reading_details, 'get_details', side_effect=lambda doc, obj: details(obj)):
            anchor = reading_details.save_anchor(self.doc, self.obj, 'bar', self.obj['id'] + ':bar:1',
                                                 correction={'metric': 'Accuracy', 'unit': '%'})
        original = copy.deepcopy(self.doc)
        with patch.object(layout_objects, 'get_layout', return_value={'objects': [self.obj]}), \
                patch.object(provider, 'configured', return_value=True), \
                patch.object(provider, 'complete', side_effect=answer) as model, \
                patch.object(parser, 'png_bytes', return_value=b'local-png'):
            first = api.ask(self.doc['id'], api.Question(question='解释这一根', mode='visual', page=1,
                            anchor_id=anchor['id'], box=[.1, .1, .9, .9]))
            followup = api.ask(self.doc['id'], api.Question(question='刚才的单位是什么', mode='visual', page=1,
                               anchor_id=anchor['id']))
            with self.assertRaises(HTTPException) as rejected:
                api.ask(self.doc['id'], api.Question(question='另一个页面', mode='visual', page=2,
                        anchor_id=anchor['id']))
            self.assertEqual(rejected.exception.status_code, 400)
            self.assertEqual(model.call_count, 2)
        self.assertEqual(first['visual_box'], anchor['bbox'])
        self.assertEqual(followup['visual_box'], anchor['bbox'])
        self.assertEqual(first['context_key'], followup['context_key'])
        self.assertEqual(captured[0]['focus_region']['local_detail']['correction'], anchor['correction'])
        self.assertEqual(captured[0]['previous_turns'], [])
        self.assertEqual(captured[1]['previous_turns'],
                         [{'question': '解释这一根', 'answer': '这根柱子的局部解释。'}])
        after = store.get(self.doc['id'])
        self.assertEqual(after['blocks'], original['blocks'])
        self.assertEqual(after['notes'], original['notes'])
        self.assertEqual(after['chats'][0], original['chats'][0])

    def test_anchor_api_rejects_unknown_item_or_invalid_correction_without_changing_document(self):
        with patch.object(reading_details, 'object_for', return_value=self.obj), \
                patch.object(reading_details, 'get_details', return_value=details(self.obj)):
            for value in (
                api.DetailAnchor(object_id=self.obj['id'], kind='bar', item_id='absent'),
                api.DetailAnchor(object_id=self.obj['id'], kind='bar', item_id=self.obj['id'] + ':bar:1',
                                 correction={'bbox': 'override'}),
                api.DetailAnchor(object_id=self.obj['id'], kind='bar', item_id=self.obj['id'] + ':bar:1',
                                 correction={'value': 42}),
            ):
                with self.subTest(value=value), self.assertRaises(HTTPException) as rejected:
                    api.create_detail_anchor(self.doc['id'], value)
                self.assertEqual(rejected.exception.status_code, 400)
        self.assertEqual(store.get(self.doc['id']), self.doc)


if __name__ == '__main__':
    unittest.main()
