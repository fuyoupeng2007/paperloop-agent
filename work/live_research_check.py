import json, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs/paper-reader'))
from backend import research, store
doc_id='f9091cd15d594b599bcf882010ec1479'
print(research.start(doc_id),flush=True)
while doc_id in research.ACTIVE:
    time.sleep(1)
result=store.get(doc_id)['research']
Path(__file__).with_name('live-paper-research.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':result['status'],'error':result.get('error'),'queries':result.get('queries'),'publication':result.get('report',{}).get('publication'),'sources':result.get('report',{}).get('sources')},ensure_ascii=False),flush=True)
