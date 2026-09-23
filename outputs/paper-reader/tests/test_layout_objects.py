"""Figure/table selection regions remain separate from existing text blocks."""

import copy
import unittest

from backend import layout_objects


def caption(kind, number, page, box, suffix):
    return {'id': f'p{page}-{suffix}', 'page': page, 'kind': 'caption',
            'bbox': box, 'text': f'{kind} {number}: Example caption'}


class LayoutObjects(unittest.TestCase):
    def test_overlapping_pdf_image_draws_create_one_figure_without_caption(self):
        doc = {'blocks': [caption('Figure', 1, 1, [.17, .55, .83, .59], 'caption')]}
        images = {1: [[.15, .34, .79, .54], [.18, .34, .84, .54], [.16, .34, .81, .54]]}
        original = copy.deepcopy(doc)
        objects = layout_objects.derive_layout(doc, images)
        self.assertEqual(doc, original)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]['id'], 'figure-p1-caption')
        self.assertEqual(objects[0]['bbox'], [.15, .34, .84, .54])
        self.assertLess(objects[0]['bbox'][3], doc['blocks'][0]['bbox'][1])
        self.assertEqual(objects[0]['confidence'], 'detected')

    def test_raster_and_vector_parts_form_one_figure(self):
        blocks = [
            {'id': 'chart', 'page': 2, 'kind': 'table', 'bbox': [.18, .68, .82, .79], 'text': 'chart'},
            caption('Figure', 2, 2, [.17, .8, .83, .85], 'caption'),
        ]
        objects = layout_objects.derive_layout({'blocks': blocks}, {2: [[.11, .65, .63, .83]]})
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]['kind'], 'figure')
        self.assertEqual(objects[0]['bbox'], [.11, .65, .82, .794])

    def test_table_object_links_existing_block_without_reparsing_cells(self):
        table = {'id': 'corrected-table', 'page': 5, 'kind': 'table',
                 'bbox': [.17, .47, .82, .716], 'text': 'manually corrected',
                 'cells': [['original ✓']], 'translation': '人工修正'}
        doc = {'blocks': [table, caption('Table', 1, 5, [.17, .719, .83, .75], 'caption')]}
        original = copy.deepcopy(doc)
        objects = layout_objects.derive_layout(doc, {})
        self.assertEqual(doc, original)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]['block_id'], 'corrected-table')
        self.assertEqual(objects[0]['bbox'], table['bbox'])
        self.assertEqual(objects[0]['label'], '表 1')

    def test_vector_chart_uses_nearby_chart_area_instead_of_full_paragraph(self):
        blocks = [
            {'id': 'body', 'page': 10, 'kind': 'text', 'bbox': [.17, .44, .83, .48], 'text': 'body'},
            {'id': 'plot-label', 'page': 10, 'kind': 'text', 'bbox': [.19, .505, .26, .516], 'text': 'EN ZH'},
            {'id': 'chart', 'page': 10, 'kind': 'table', 'bbox': [.26, .55, .78, .685], 'text': 'values'},
            {'id': 'axis', 'page': 10, 'kind': 'text', 'bbox': [.2, .68, .8, .703], 'text': 'axis'},
            caption('Figure', 6, 10, [.17, .717, .83, .743], 'caption'),
        ]
        obj = layout_objects.derive_layout({'blocks': blocks}, {})[0]
        self.assertEqual(obj['bbox'], [.19, .505, .8, .703])
        self.assertEqual(obj['source'], 'chart-region')
        self.assertEqual(obj['confidence'], 'estimated')

    def test_caption_above_table_and_figure(self):
        blocks = [
            caption('Table', 4, 3, [.17, .2, .83, .24], 'table-caption'),
            {'id': 'table-below', 'page': 3, 'kind': 'table',
             'bbox': [.25, .27, .76, .46], 'text': 'cells'},
            {'id': 'row-label', 'page': 3, 'kind': 'text',
             'bbox': [.18, .28, .24, .45], 'text': 'labels'},
            caption('Figure', 7, 3, [.17, .55, .83, .59], 'figure-caption'),
        ]
        objects = layout_objects.derive_layout({'blocks': blocks},
                                                {3: [[.18, .62, .82, .85]]})
        table, figure = objects
        self.assertEqual(table['label'], '表 4')
        self.assertEqual(table['bbox'], [.18, .27, .76, .46])
        self.assertEqual(table['block_id'], 'table-below')
        self.assertEqual(figure['label'], '图 7')
        self.assertEqual(figure['bbox'], [.18, .62, .82, .85])


if __name__ == '__main__':
    unittest.main()
