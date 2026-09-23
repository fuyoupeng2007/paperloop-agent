import json
from pathlib import Path
import httpx

root='http://127.0.0.1:8765/api/documents/f9091cd15d594b599bcf882010ec1479'
with httpx.Client(timeout=360,trust_env=False,headers={'X-PaperLoop':'1'}) as client:
    doc=client.get(root).json()
    target=next(b for b in doc['blocks'] if b['page']==1 and b['kind']=='text' and 'triplet' in b['text'].lower())
    before=doc['reading_page']
    answers=[]
    for question in ('这一段中的 SRR 三个部分各自做什么？请简洁解释。','第三个部分为什么不能省略？请结合刚才这段继续说明。'):
        result=client.post(root+'/ask',json={'question':question,'scope':'selection','block_id':target['id'],'page':1})
        result.raise_for_status()
        answer=result.json()
        assert answer['context_key']=='selection:'+target['id']
        assert answer['citations']
        assert '未获得可核对的引用' not in answer['answer']
        answers.append(answer)
        print(json.dumps({'id':answer['id'],'context':answer['context_key'],'citations':len(answer['citations'])}),flush=True)
    current=client.get(root).json()
    assert current['reading_page']==before
Path(__file__).with_name('live-followup-result.json').write_text(json.dumps(answers,ensure_ascii=False,indent=2),encoding='utf-8')
print('Two real paragraph turns passed; reading position unchanged.',flush=True)
