import json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs/paper-reader'))
from backend import codex_bridge
result,usage=codex_bridge.request([{'role':'user','content':'Search the web for the official MonkeyOCR repository by Yuliang Liu and open https://github.com/Yuliang-Liu/MonkeyOCR . Verify the latest original MonkeyOCR publication venue. Report the venue and URL briefly.'}],timeout=200,web_search=True)
Path(__file__).with_name('probe-web-result.json').write_text(json.dumps({'result':result,'usage':usage},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'result':result,'usage':usage},ensure_ascii=False))
