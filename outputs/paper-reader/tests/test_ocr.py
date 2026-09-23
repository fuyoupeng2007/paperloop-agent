import tempfile
import os
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from backend import parser

class ScannedPDF(unittest.TestCase):
    def test_scanned_page(self):
        temp=Path(tempfile.mkdtemp(dir=os.environ.get('PAPERLOOP_TEST_WORK',tempfile.gettempdir())))
        image=Image.new('RGB',(1100,1400),'white')
        draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',39)
        lines=['A STUDY OF PAPER READING','Abstract','We evaluated a reading method in 2026.','The study used 30 papers.','Results showed better comprehension.']
        for i,line in enumerate(lines):
            draw.text((100,120+i*90),line,font=font,fill='black')
        pdf=temp/'scan.pdf'
        image.save(pdf,'PDF',resolution=150)
        result=parser.parse_page(pdf,1)
        self.assertEqual(result['source'],'ocr',result['warning'])
        content=' '.join(b['text'] for b in result['blocks'])
        self.assertIn('2026',content)
        self.assertTrue(all(len(b['bbox'])==4 for b in result['blocks']))

if __name__=='__main__':
    unittest.main()
