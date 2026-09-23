"""Layout regressions: rotated arXiv stamps, wrapped titles and column order."""
import os
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend import parser


def write_pdf(path, content):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(content.encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def word(text, x0, top, width=85, size=10):
    return {'text': text, 'x0': x0, 'x1': x0+width, 'top': top, 'bottom': top+size, 'size': size}


class PaperLayout(unittest.TestCase):
    def test_rotated_arxiv_stamp_does_not_pollute_title_or_abstract(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PAPERLOOP_TEST_WORK')) as temp:
            path = Path(temp)/'layout.pdf'
            write_pdf(path, '''BT /F1 18 Tf 130 690 Td (Document Parsing with a) Tj ET
BT /F1 18 Tf 130 669 Td (Structure Recognition Paradigm) Tj ET
BT /F1 10 Tf 110 620 Td (Alice Example and Bob Researcher) Tj ET
BT /F1 12 Tf 110 590 Td (Abstract) Tj ET
BT /F1 10 Tf 110 560 Td (We introduce a reliable reading system.) Tj ET
BT /F1 10 Tf 110 547 Td (It preserves the order of all document text.) Tj ET
q 0 1 -1 0 32 240 cm BT /F1 14 Tf 0 0 Td (arXiv:2506.05218v1 [cs.CV] 5 Jun 2025) Tj ET Q''')
            result = parser.parse_page(path, 1)
            blocks = result['blocks']
            self.assertEqual(parser.title_from_blocks(blocks), 'Document Parsing with a Structure Recognition Paradigm')
            headings = [b['text'] for b in blocks if b['kind'] == 'heading']
            self.assertEqual(headings, ['Abstract'])
            self.assertEqual([b['text'] for b in blocks if b['kind'] == 'title'], ['Document Parsing with a Structure Recognition Paradigm'])
            self.assertEqual([b['text'] for b in blocks if b['kind'] == 'metadata'], ['Alice Example and Bob Researcher'])
            margins = [b for b in blocks if b['kind'] == 'margin']
            self.assertEqual(len(margins), 1)
            self.assertEqual(margins[0]['text'], 'arXiv:2506.05218v1 [cs.CV] 5 Jun 2025')
            self.assertEqual(margins[0]['status'], 'preserved')
            body = ' '.join(b['text'] for b in blocks if b['kind'] == 'text')
            self.assertIn('We introduce a reliable reading system. It preserves the order of all document text.', body)
            self.assertNotIn('arXiv', body)

    def test_rotated_interior_content_is_kept_for_translation(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PAPERLOOP_TEST_WORK')) as temp:
            path = Path(temp)/'vertical-label.pdf'
            write_pdf(path, '''BT /F1 10 Tf 90 700 Td (Body text describes experimental evaluation and measurements.) Tj ET
q 0 1 -1 0 200 300 cm BT /F1 12 Tf 0 0 Td (Accuracy percent) Tj ET Q''')
            blocks = parser.parse_page(path, 1)['blocks']
            label = next(b for b in blocks if 'Accuracy percent' in b['text'])
            self.assertEqual(label['kind'], 'text')
            self.assertEqual(label['status'], 'pending')

    def test_ordinary_sentences_and_affiliations_are_not_section_headings(self):
        for text in ('methods to deep learning-based techniques, enabling significant improvements in robustness and', 'Results show that the model outperforms the previous approach.', '1 Huazhong University of Science and Technology, 2 Kingsoft Office'):
            self.assertEqual(parser.classify(text, 10, 10), 'text', text)
        for text in ('Abstract', '2 Related Work', '3.1 Encoder and Decoder Stacks', '5.3 Implement Details'):
            self.assertEqual(parser.classify(text, 10, 10), 'heading', text)

    def test_two_column_order_keeps_every_word_once(self):
        words = [word('Full width paper heading', 220, 40, width=180, size=18)]
        for row in range(7):
            words.extend([word(f'Left_column_content_{row}', 70, 100+row*13, width=205), word(f'Right_column_content_{row}', 330, 100+row*13, width=205)])
        regions, columns = parser.ordered_regions(words, 612)
        self.assertTrue(columns)
        ordered = [w['text'] for region in regions for w in region]
        self.assertEqual(ordered, ['Full width paper heading']+[f'Left_column_content_{i}' for i in range(7)]+[f'Right_column_content_{i}' for i in range(7)])
        self.assertEqual(len({id(w) for region in regions for w in region}), len(words))

    def test_numeric_tables_do_not_split_a_single_column_paper(self):
        words = []
        for row in range(8):
            words.extend([word('0.234 0.567', 160, 100+row*13), word('0.891 0.123', 330, 100+row*13)])
        words.append(word('A continuous body paragraph crosses the centre of the page.', 100, 230, width=420))
        regions, columns = parser.ordered_regions(words, 612)
        self.assertFalse(columns)
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0]), len(words))


if __name__ == '__main__':
    unittest.main()
