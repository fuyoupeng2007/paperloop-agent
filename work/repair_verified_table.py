"""One-paper correction verified against the user's original Table 1 rendering."""
import json
import sqlite3
import sys
from pathlib import Path

project=Path(__file__).resolve().parents[1]/'outputs'/'paper-reader'
sys.path.insert(0,str(project))
from backend import store

doc_id='f9091cd15d594b599bcf882010ec1479'
doc=store.get(doc_id)
assert doc['filename']=='MonkeyOCR_2025_v1.pdf'
assert doc['status'] not in ('queued','parsing','translating')
with sqlite3.connect(store.DATA/'reader.db') as source, sqlite3.connect(Path(__file__).parent/'reader-before-table-repair.db') as backup:
    source.backup(backup)

groups=[
    ('Layout Detection Dataset','版面检测数据集',[
        ['M⁶Doc [6]','6','✓','','','','','✓','✓'],
        ['CDLA [18]','1','✓','','','','','','✓'],
        ['D4LA [8]','5','✓','','','','','✓',''],
        ['DocLayNet [37]','2','✓','','','','','✓','✓']]),
    ('Fomula Recognition Dataset','公式识别数据集',[
        ['Unimernet [42]','-','','','✓','','','',''],
        ['TexTeller [31]','-','','','✓','','','','']]),
    ('Table Recognition Dataset','表格识别数据集',[
        ['FinTabNet [51]','-','','','','✓','','',''],
        ['PubTabNet [52]','-','','','','✓','','','']]),
    ('Comprehensive Dataset','综合数据集',[
        ['DocGenome [46]','1','✓','✓','✓','✓','✓','✓',''],
        ['MonkeyDoc','>10','✓','✓','✓','✓','✓','✓','✓']]),
]
en=['Dataset | Document Domain | Layout Detection | Reading Order Prediction | Fomula Recognition | Table Recognition | Text Recognition | EN | ZH']
zh=['数据集 | 文档领域数 | 版面检测 | 阅读顺序预测 | 公式识别 | 表格识别 | 文本识别 | 英文 | 中文']
for english,chinese,rows in groups:
    en.extend([english]+[' | '.join(row) for row in rows])
    zh.extend([chinese]+[' | '.join(row) for row in rows])

def repair(value):
    by_id={b['id']:b for b in value['blocks']}
    table=by_id['p5-97695a748e-3']
    assert '(cid:33)' in table['text']
    assert 'MonkeyDoc' in by_id['p5-83a7b90386-4']['text']
    table.update(kind='table',text='\n'.join(en),translation='\n'.join(zh),status='done',error='',
                 bbox=[.176,.468,.817,.716],cells=[row for _,_,rows in groups for row in rows],
                 review_note='本表已对照原 PDF 核对并重建；空单元格保持原样。')
    removed='p5-83a7b90386-4'
    value['blocks']=[b for b in value['blocks'] if b['id']!=removed]
    for note in value['notes']:
        if note.get('block_id')==removed:
            note['block_id']=table['id']
    for chat in value['chats']:
        for citation in chat.get('citations',[]):
            if citation['block_id']==removed:
                citation['stale']=True
    page=next(p for p in value['pages'] if p['number']==5)
    if all('(cid:' not in b['text'] for b in value['blocks'] if b['page']==5):
        page['warning']=''
    for block in value['blocks']:
        if block['status']=='failed':
            block.update(status='pending',attempts=0,error='')
    value.update(status='queued',message='表格已按原页核对，正在完成网址与专名的最后检查。')
    store.event(value,'原页核对修正表 1：bbding 字体的勾选符号、DocGenome 行及合并单元格。其余译文保持不变。')

store.change(doc_id,repair)
print('Verified Table 1 repaired; remaining blocks queued.')
