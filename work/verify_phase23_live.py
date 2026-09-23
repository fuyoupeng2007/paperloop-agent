"""One meaningful real-account visual check, preserving old paper content."""
import json
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[1]
doc_id='f9091cd15d594b599bcf882010ec1479'
with httpx.Client(base_url='http://127.0.0.1:8765/api',headers={'X-PaperLoop':'1'},timeout=420) as client:
    before=client.get('/documents/'+doc_id).raise_for_status().json()
    objects=client.get('/documents/'+doc_id+'/layout').raise_for_status().json()['objects']
    obj=next(x for x in objects if x['kind']=='figure' and x['page']==1)
    details=client.get('/documents/'+doc_id+'/objects/'+obj['id']+'/details').raise_for_status().json()
    mark=next(x for x in details['marks'] if x.get('metric')=='Pages/s' and x.get('series')=='MonkeyOCR-3B')
    anchor=client.post('/documents/'+doc_id+'/anchors',json={'object_id':obj['id'],'kind':'bar','item_id':mark['id']}).raise_for_status().json()
    answer=client.post('/documents/'+doc_id+'/ask',json={'question':'这根柱子的 0.84 表示什么？为什么不能直接与 Overall 的 84.5 比较？请根据图例和坐标轴说明。','mode':'visual','scope':'page','page':1,'box':anchor['bbox'],'anchor_id':anchor['id']}).raise_for_status().json()
    after=client.get('/documents/'+doc_id).raise_for_status().json()
    assert after['blocks']==before['blocks'] and after['notes']==before['notes']
    assert after['chats'][:len(before['chats'])]==before['chats']
    assert after['reading_page']==before['reading_page']
    assert answer['context_key']=='object:'+anchor['id'] and answer['visual_box']==anchor['bbox']
    (root/'work'/'phase23-live-visual.json').write_text(json.dumps(answer,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'answer':answer['answer'],'context_key':answer['context_key'],'preserved_blocks':len(after['blocks']),'reading_page':after['reading_page']},ensure_ascii=False))
