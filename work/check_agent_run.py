"""Read-only HTTP acceptance check for an existing real or isolated agent run.

Does not start/resume research, alter papers, or invoke any model. Optionally
accepts a saved pre-run document JSON for source-content preservation checks.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx


def verify(base, doc_id, run_id, baseline=None):
    parsed = urlparse(base)
    assert parsed.hostname in ('127.0.0.1', 'localhost', '::1'), 'Use a local PaperLoop service'
    with httpx.Client(base_url=base, timeout=30, trust_env=False, follow_redirects=False) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response

        doc = get('/api/documents/' + doc_id).json()
        prefix = '/api/documents/' + doc_id + '/agent-runs/' + run_id
        run = get(prefix).json()
        assert run['doc_id'] == doc_id
        assert run['id'] == run_id
        assert run['status'] != 'running', 'Wait for the current model request before acceptance checking'
        assert len(run['steps']) == run['steps_used']
        assert run['steps_used'] <= run['max_steps']
        assert run['calls_used'] <= run['max_calls']
        evidence = {entry['id']: entry for entry in run['evidence']}
        assert len(evidence) == len(run['evidence']), 'Evidence IDs must be unique'
        blocks = {block['id']: block for block in doc['blocks']}
        for entry in evidence.values():
            assert entry['quote'].strip()
            if entry['type'] == 'paper':
                source = blocks[entry['block_id']]
                assert entry['page'] == source['page'], 'Evidence page must match its source block'
                assert ' '.join(entry['quote'].split()) in ' '.join(source['text'].split()), 'Evidence quote differs from saved PDF text'
            elif entry['type'] == 'visual':
                assert 1 <= entry['page'] <= doc['page_count']
                box = entry['box']
                assert len(box) == 4 and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in box)
                assert box[0] < box[2] and box[1] < box[3]
            elif entry['type'] == 'web':
                assert urlparse(entry['url']).scheme in ('https', 'http')
                assert any(step.get('observation', {}).get('web_activity') for step in run['steps']), 'Web evidence requires actual recorded search activity'
            else:
                raise AssertionError('Unexpected evidence type')
        for step in run['steps']:
            assert step['status'] != 'running', 'Stopped run must settle the in-flight step'
            assert set(step.get('observation', {}).get('evidence_ids', [])) <= evidence.keys()
        for claim in run['claims']:
            assert claim['evidence_ids'], 'Claim must cite observed evidence'
            assert set(claim['evidence_ids']) <= evidence.keys()
        if run['status'] == 'completed':
            assert run['claims'], 'Completed run must have verified conclusions'
            assert run['review']['goal_met'] is True
            assert not run['review']['missing'], 'Completed run must have no outstanding review gaps'
            assert all(item['supported'] is True for item in run['review']['checks'])
        if baseline:
            original = json.loads(Path(baseline).read_text(encoding='utf-8'))
            for key in ('blocks', 'notes', 'chats', 'glossary', 'reading_page'):
                assert doc.get(key) == original.get(key), 'Protected document field changed: ' + key
        html = get(prefix + '/export?format=html')
        markdown = get(prefix + '/export?format=md')
        assert 'text/html' in html.headers['content-type']
        assert 'text/markdown' in markdown.headers['content-type']
        assert 'attachment;' in html.headers['content-disposition']
        assert '<script' not in html.text.lower()
        assert all(entry_id in html.text for entry_id in evidence)
        summary = {'status': run['status'], 'steps': run['steps_used'], 'model_calls': run['calls_used'],
                   'evidence': dict(Counter(e['type'] for e in evidence.values())),
                   'claims': len(run['claims']), 'exports': {'html_bytes': len(html.content), 'md_bytes': len(markdown.content)},
                   'source_baseline_checked': bool(baseline)}
        print(json.dumps(summary, ensure_ascii=False))
        return summary


if __name__ == '__main__':
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument('--base', default='http://127.0.0.1:8765')
    arguments.add_argument('--doc', required=True)
    arguments.add_argument('--run', required=True)
    arguments.add_argument('--baseline')
    options = arguments.parse_args()
    verify(options.base, options.doc, options.run, options.baseline)
