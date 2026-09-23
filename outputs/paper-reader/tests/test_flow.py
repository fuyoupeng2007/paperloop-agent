"""Integration check: real PDF upload/parsing/worker, local fake Chat Completions server."""
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TEST_WORK=Path(os.environ.get('PAPERLOOP_TEST_WORK',tempfile.gettempdir()))
TEST_WORK.mkdir(parents=True,exist_ok=True)
os.environ['PAPERLOOP_DATA']=tempfile.mkdtemp(prefix='paperloop-test-',dir=TEST_WORK)
from fastapi.testclient import TestClient
from backend.app import app

def paper():
    writer=PdfWriter()
    page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    ref=writer._add_object(font)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):ref})})
    stream=DecodedStreamObject()
    stream.set_data(b'BT /F1 18 Tf 50 720 Td (A Study of Reading 2026) Tj ET\nBT /F1 12 Tf 50 690 Td (Abstract) Tj ET\nBT /F1 11 Tf 50 650 Td (We evaluate a reading method with 30 papers and report clear outcomes.) Tj ET\nBT /F1 11 Tf 50 630 Td (The proposed method improves comprehension by 12 percent.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    data=io.BytesIO();writer.write(data)
    return data.getvalue()

class FakeModel(BaseHTTPRequestHandler):
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        request=payload['messages'][-1]['content']
        if isinstance(request,list):
            request=request[0]['text']
        info=json.loads(request) if request.startswith('{') else {'task':'expand'}
        if info['task']=='glossary':
            answer={'terms':[]}
        elif info['task']=='translate':
            answer={'translation':'这是忠实的中文翻译：'+info['source']}
        elif info['task']=='expand':
            answer='reading method papers comprehension'
        else:
            evidence=info['evidence']
            answer={'answer':'论文评价了阅读方法。','citations':[{'block_id':evidence[0]['block_id'],'quote':evidence[0]['text'][:20]}],'supplement':''}
        response={'choices':[{'message':{'content':answer if isinstance(answer,str) else json.dumps(answer)},'finish_reason':'stop'}],'usage':{'prompt_tokens':25,'completion_tokens':20}}
        body=json.dumps(response).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):
        return

class Flow(unittest.TestCase):
    def wait_terminal(self,client,doc_id):
        end=time.monotonic()+20
        while time.monotonic()<end:
            doc=client.get('/api/documents/'+doc_id).json()
            if doc['status'] in ('completed','review','blocked'):
                return doc
            time.sleep(.15)
        self.fail('Task did not finish: '+doc['message'])

    def test_end_to_end(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),FakeModel)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        headers={'X-PaperLoop':'1'}
        try:
            with TestClient(app) as client:
                config={'provider':'api','base_url':f'http://127.0.0.1:{server.server_port}/v1','api_key':'test-secret','model':'fake','vision_model':'fake','call_limit':1}
                response=client.put('/api/settings',headers=headers,json=config)
                self.assertEqual(response.status_code,200,response.text)
                self.assertNotIn('test-secret',response.text)
                content=paper()
                response=client.post('/api/documents',headers=headers,files={'file':('sample.pdf',content,'application/pdf')})
                self.assertEqual(response.status_code,200,response.text)
                doc_id=response.json()['id']
                duplicate=client.post('/api/documents',headers=headers,files={'file':('copy.pdf',content,'application/pdf')}).json()
                self.assertEqual(duplicate['id'],doc_id)
                self.assertTrue(duplicate['duplicate'])
                doc=self.wait_terminal(client,doc_id)
                self.assertEqual(doc['status'],'blocked')
                self.assertEqual(doc['usage']['calls'],1)
                config['call_limit']=50
                client.put('/api/settings',headers=headers,json=config)
                client.post(f'/api/documents/{doc_id}/task',headers=headers,json={'action':'resume'})
                doc=self.wait_terminal(client,doc_id)
                self.assertIn(doc['status'],('completed','review'),doc['message'])
                self.assertGreater(len(doc['blocks']),0)
                self.assertTrue(any(b['status']=='done' for b in doc['blocks']))
                self.assertEqual(client.get(f'/api/documents/{doc_id}/pages/1/image').status_code,200)
                block=next(b for b in doc['blocks'] if b['status']=='done')
                self.assertEqual(len(block['bbox']),4)
                ask=client.post(f'/api/documents/{doc_id}/ask',headers=headers,json={'question':'What was evaluated?','scope':'selection','block_id':block['id']})
                self.assertEqual(ask.status_code,200,ask.text)
                self.assertTrue(ask.json()['citations'])
                note=client.post(f'/api/documents/{doc_id}/notes',headers=headers,json={'text':'Key result','block_id':block['id'],'page':1})
                self.assertEqual(note.status_code,200,note.text)
                self.assertIn('Key result',client.get(f'/api/documents/{doc_id}/export?format=md').text)
                before=client.get('/api/documents/'+doc_id).json()['usage']['calls']
                self.assertEqual(client.post(f'/api/documents/{doc_id}/task',headers=headers,json={'action':'pause'}).status_code,200)
                self.assertEqual(client.post(f'/api/documents/{doc_id}/task',headers=headers,json={'action':'resume'}).status_code,200)
                self.assertEqual(self.wait_terminal(client,doc_id)['usage']['calls'],before)
                self.assertEqual(client.get(f'/api/documents/{doc_id}/pdf').headers['content-type'],'application/pdf')
        finally:
            server.shutdown()
            server.server_close()

if __name__=='__main__':
    unittest.main()
