"""Create a two-page synthetic PDF for the browser smoke test."""
import sys
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

writer=PdfWriter()
for number in (1,2):
    page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=DecodedStreamObject()
    title='PaperLoop Browser Study' if number==1 else '2 Results'
    content.set_data((f'BT /F1 18 Tf 50 720 Td ({title}) Tj ET\n'
        f'BT /F1 11 Tf 50 660 Td (This is page {number} of a synthetic reading study.) Tj ET\n'
        'BT /F1 11 Tf 50 640 Td (The study evaluates 30 papers and records comprehension.) Tj ET').encode())
    page[NameObject('/Contents')]=writer._add_object(content)
writer.add_metadata({'/Title':'PaperLoop Browser Study'})
target=Path(sys.argv[1])
target.parent.mkdir(parents=True,exist_ok=True)
with target.open('wb') as output:
    writer.write(output)
