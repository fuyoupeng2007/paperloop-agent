"""Isolated PaperLoop UI smoke server. All model answers are local fixtures."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

WORK = Path(__file__).resolve().parent
PROJECT = WORK.parent / 'outputs' / 'paper-reader'
TEST_DATA = WORK / 'phase23-testdata'
assert TEST_DATA.resolve().parent == WORK.resolve()
assert (TEST_DATA / 'reader.db').is_file(), 'Prepare isolated database copy first'
os.environ['PAPERLOOP_DATA'] = str(TEST_DATA)
sys.path.insert(0, str(PROJECT))

from backend import app as api
from backend import codex_bridge, provider, research, study, worker

requests = []


def complete(doc_id, messages, **kwargs):
    content = messages[-1]['content']
    if isinstance(content, list):
        content = content[0]['text']
    data = json.loads(content)
    assert data['task'] == 'visual', 'This fixture only allows visual QA'
    requests.append(data)
    return json.dumps({'answer': '本机测试回答：已收到所选局部及完整上下文。',
                       'citations': [], 'supplement': ''}, ensure_ascii=False)


provider.complete = complete
provider.configured = lambda _: True
codex_bridge.status = lambda *args, **kwargs: {
    'available': True, 'logged_in': True, 'connected': True, 'message': 'Local test fixture'}
codex_bridge.remember_installation = lambda: None
worker.start = lambda: SimpleNamespace(join=lambda **kwargs: None)
research.recover_interrupted = lambda: None
study.recover_interrupted = lambda: None


@api.app.get('/__test/model-inputs')
def inputs():
    return requests


# The production SPA catch-all is already mounted; expose this local-only
# inspection route before it without modifying the app source.
api.app.router.routes.insert(0, api.app.router.routes.pop())


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(api.app, host='127.0.0.1', port=8766, log_level='warning')
