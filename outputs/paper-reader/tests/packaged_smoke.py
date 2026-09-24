"""Exercise a running isolated packaged backend, using synthetic PDFs and a fake API.

Usage: python tests/packaged_smoke.py http://127.0.0.1:8773 work/result.json
Never point this test at a user's data directory: it requires an empty library.
"""
import io
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


class FakeModel(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        type(self).requests += 1
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content = payload['messages'][-1]['content']
        if isinstance(content, list):
            content = content[0]['text']
        info = json.loads(content) if content.startswith('{') else {}
        task = info.get('task')
        if task == 'glossary':
            answer = {'terms': []}
        elif task == 'translate_batch':
            answer = {'translations': [{'id': item['id'], 'translation': '测试中文译文：' + item['source']} for item in info['blocks']]}
        elif task == 'translate':
            answer = {'translation': '测试中文译文：' + info['source']}
        elif info.get('evidence'):
            item = info['evidence'][0]
            answer = {'answer': '这是隔离测试的论文回答。', 'citations': [{'block_id': item['block_id'], 'quote': item['text'][:20]}], 'supplement': ''}
        else:
            answer = '连接成功 reading study'
        body = json.dumps({'choices': [{'message': {'content': answer if isinstance(answer, str) else json.dumps(answer)}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 18 Tf 50 720 Td (A Packaged Reading Study 2026) Tj ET\nBT /F1 11 Tf 50 670 Td (We evaluated 30 papers and found improved comprehension.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def scanned_pdf():
    picture = Image.new('RGB', (1100, 1400), 'white')
    draw = ImageDraw.Draw(picture)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 39)
    for index, line in enumerate(['A STUDY OF PAPER READING', 'Abstract', 'We evaluated a reading method in 2026.', 'The study used 30 papers.', 'Results showed better comprehension.']):
        draw.text((100, 120 + index * 90), line, font=font, fill='black')
    result = io.BytesIO()
    picture.save(result, 'PDF', resolution=150)
    return result.getvalue()


def run(address):
    result = {}
    api = httpx.Client(base_url=address, headers={'X-PaperLoop': '1'}, timeout=120, trust_env=False)
    result['health'] = api.get('/api/health').raise_for_status().json()
    assert result['health']['edition'] == 'distributed'
    assert api.get('/api/documents').json() == [], 'Use a fresh isolated test data directory.'
    config = api.get('/api/settings').raise_for_status().json()
    assert config['provider'] == 'api' and not config['has_key'] and not config['configured']
    assert config['model'] == 'deepseek-flash'
    index = api.get('/').raise_for_status().text
    for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index):
        assert api.get(asset).status_code == 200

    def wait(doc_id, states):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            doc = api.get('/api/documents/' + doc_id).raise_for_status().json()
            if doc['status'] in states:
                return doc
            time.sleep(.3)
        raise AssertionError(f'Timeout: {doc["message"]}')

    doc_id = api.post('/api/documents', files={'file': ('text.pdf', text_pdf(), 'application/pdf')}).raise_for_status().json()['id']
    doc = wait(doc_id, {'awaiting_model', 'blocked'})
    assert doc['blocks'] and doc['usage']['calls'] == 0
    result['read_without_key'] = True
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = {'provider': 'api', 'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'isolated-fake-key', 'model': 'fake', 'vision_model': 'fake'}
        assert api.post('/api/settings/check', json=settings).raise_for_status().json()['ok']
        assert not api.get('/api/settings').json()['has_key']
        saved = api.put('/api/settings', json=settings).raise_for_status()
        assert saved.json()['has_key'] and 'isolated-fake-key' not in saved.text
        api.post(f'/api/documents/{doc_id}/task', json={'action': 'resume'}).raise_for_status()
        doc = wait(doc_id, {'completed', 'review'})
        translated = [item for item in doc['blocks'] if item['status'] == 'done']
        assert translated
        result['translated_blocks'] = len(translated)
        assert api.get(f'/api/documents/{doc_id}/pages/1/image').status_code == 200
        ask = api.post(f'/api/documents/{doc_id}/ask', json={'question': 'What was studied?', 'scope': 'selection', 'block_id': translated[0]['id']}).raise_for_status().json()
        assert ask['citations']
        api.post(f'/api/documents/{doc_id}/notes', json={'text': 'Packaged persistence note', 'block_id': translated[0]['id'], 'page': 1}).raise_for_status()
        assert 'Packaged persistence note' in api.get(f'/api/documents/{doc_id}/export?format=md').text
        scan_id = api.post('/api/documents', files={'file': ('scan.pdf', scanned_pdf(), 'application/pdf')}).raise_for_status().json()['id']
        scan = wait(scan_id, {'completed', 'review', 'blocked'})
        assert any('2026' in item['text'] for item in scan['blocks']), scan['message']
        result['scanned_ocr_blocks'] = len(scan['blocks'])
        result['fake_model_requests'] = FakeModel.requests
        result['question_citations'] = len(ask['citations'])
        result['persisted_document'] = doc_id
        result['ok'] = True
    finally:
        api.put('/api/settings', json={'api_key': '', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'vision_model': 'deepseek-flash'})
        server.shutdown()
        server.server_close()
        api.close()
    return result


if __name__ == '__main__':
    outcome = run(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(outcome, ensure_ascii=False))
