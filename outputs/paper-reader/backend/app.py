import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field

from . import agent_loop, agent_report, agent_tools, codex_bridge, layout_objects, parser, provider, reading_details, research, store, study, worker

@asynccontextmanager
async def lifespan(app):
    store.init()
    codex_bridge.remember_installation()
    lock=FileLock(str(store.DATA/'server.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError('本数据目录已有运行中的服务，请勿启动多个 worker。') from exc
    thread=worker.start()
    research.recover_interrupted()
    study.recover_interrupted()
    agent_loop.recover_interrupted()
    yield
    worker.STOP.set()
    worker.WAKE.set()
    thread.join(timeout=2)
    lock.release()

app=FastAPI(title='PaperLoop',lifespan=lifespan)
UPLOAD_LOCK=threading.Lock()
CHAT_LOCK=threading.Lock()

@app.middleware('http')
async def local_only(request:Request,call_next):
    hostname=request.url.hostname
    if hostname not in ('127.0.0.1','localhost','::1','testserver'):
        return JSONResponse({'detail':'仅允许本机访问。'},status_code=403)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).hostname not in ('127.0.0.1','localhost','::1','testserver'):
        return JSONResponse({'detail':'不允许跨站访问本机资料。'},status_code=403)
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('x-paperloop')!='1':
        return JSONResponse({'detail':'缺少本机应用请求标识。'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

@app.exception_handler(KeyError)
async def not_found(request,exc):
    return JSONResponse({'detail':'论文或记录不存在。'},status_code=404)

@app.exception_handler(provider.ProviderError)
async def provider_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=409 if isinstance(exc,provider.BudgetError) else 502)

def public_settings():
    config=store.settings()
    return {k:v for k,v in config.items() if k!='api_key'} | {'has_key':bool(config['api_key']),'configured':provider.configured(config),'engine':codex_bridge.status()}

@app.get('/api/engine/status')
def engine_status():
    return codex_bridge.status(force=True)

@app.post('/api/engine/login')
def engine_login():
    try:
        return {'message':codex_bridge.login()}
    except codex_bridge.EngineError as exc:
        raise HTTPException(409,str(exc))

@app.post('/api/engine/check')
def engine_check():
    try:
        codex_bridge.status(force=True)
        answer,_=codex_bridge.request([{'role':'user','content':'请只回复：连接成功。'}],timeout=60)
        return {'ok':bool(answer),'message':'ChatGPT 连接成功，可以导入论文并自动翻译。'}
    except codex_bridge.EngineError as exc:
        return {'ok':False,'message':str(exc)}

@app.get('/api/health')
def health():
    return {'ok':True,'version':'0.3.0','parser':parser.PARSER_VERSION,
            'edition':'distributed' if os.environ.get('PAPERLOOP_DISTRIBUTED')=='1' else 'development'}

@app.get('/api/settings')
def get_settings():
    return public_settings()

class Settings(BaseModel):
    provider:str='codex'
    base_url:str=Field(default='',max_length=500)
    api_key:str|None=Field(default=None,max_length=1000)
    model:str=Field(default='',max_length=150)
    vision_model:str=Field(default='',max_length=150)
    call_limit:int=Field(default=500,ge=1,le=10000)
    input_price:float=Field(default=0,ge=0,le=10000)
    output_price:float=Field(default=0,ge=0,le=10000)

@app.put('/api/settings')
def put_settings(value:Settings):
    config=value.model_dump(exclude_unset=True)
    if config.get('provider',store.settings()['provider']) not in ('codex','api'):
        raise HTTPException(400,'不支持的连接方式。')
    try:
        if config.get('base_url'):
            config['base_url']=provider.validate_url(config['base_url'].strip())
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    store.save_settings(config)
    return public_settings()

@app.post('/api/settings/check')
def check_settings(value:Settings):
    candidate=store.settings() | value.model_dump(exclude_unset=True)
    if candidate.get('api_key') is None:
        candidate['api_key']=store.settings()['api_key']
    if candidate.get('provider')!='api':
        raise HTTPException(400,'请选择 API 连接方式。')
    try:
        return provider.check_api_connection(candidate)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc

def brief(doc):
    return {k:v for k,v in doc.items() if k not in ('blocks','notes','chats','events','glossary','pages','fingerprint')} | {'translated':sum(b['status']=='done' for b in doc['blocks']),'total':len(doc['blocks'])}

@app.get('/api/documents')
def documents():
    return [brief(doc) for doc in store.all_docs()]

@app.post('/api/documents')
def upload(file:UploadFile=File(...)):
    if not (file.filename or '').lower().endswith('.pdf'):
        raise HTTPException(400,'请选择 PDF 文件。')
    content=file.file.read(50*1024*1024+1)
    if len(content)>50*1024*1024:
        raise HTTPException(413,'文件超过 50 MB。')
    if not content.startswith(b'%PDF-'):
        raise HTTPException(400,'文件内容不是有效 PDF。')
    fingerprint=hashlib.sha256(content).hexdigest()
    with UPLOAD_LOCK:
        duplicate=next((d for d in store.all_docs() if d['fingerprint']==fingerprint),None)
        if duplicate:
            return {'id':duplicate['id'],'duplicate':True}
        doc_id=uuid.uuid4().hex
        path=store.DATA/(doc_id+'.pdf')
        path.write_bytes(content)
        try:
            pages,title=parser.inspect_pdf(path)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else 'PDF 无法打开，可能已损坏。')
        doc={'id':doc_id,'fingerprint':fingerprint,'title':title or file.filename[:250], 'filename':file.filename[:250], 'page_count':pages,'status':'queued','message':'已加入解析队列','created_at':time.time(),'updated_at':time.time(),'pages':[],'blocks':[],'glossary':[],'glossary_ready':False,'glossary_version':1,'notes':[],'chats':[],'events':[],'reading_page':1,'usage':{'calls':0,'input_tokens':0,'output_tokens':0,'estimated_cost':0}}
        store.insert(doc)
    worker.WAKE.set()
    return {'id':doc_id,'duplicate':False}

@app.get('/api/documents/{doc_id}')
def document(doc_id:str):
    return store.get(doc_id) | {'worker_busy':worker.is_busy(doc_id)}

@app.get('/api/documents/{doc_id}/layout')
def document_layout(doc_id:str):
    doc=store.get(doc_id)
    return layout_objects.get_layout(doc,store.DATA/(doc_id+'.pdf'))


@app.get('/api/documents/{doc_id}/objects/{object_id}/details')
def object_details(doc_id:str,object_id:str):
    doc=store.get(doc_id)
    obj=reading_details.object_for(doc,object_id)
    if not obj:
        raise HTTPException(404,'图表对象不存在。')
    return reading_details.get_details(doc,obj)


@app.get('/api/documents/{doc_id}/objects/{object_id}/table.csv')
def table_csv(doc_id:str,object_id:str):
    doc=store.get(doc_id)
    obj=reading_details.object_for(doc,object_id)
    if not obj or obj['kind']!='table':
        raise HTTPException(404,'表格不存在。')
    detail=reading_details.get_details(doc,obj)
    cells=[c for c in detail['cells'] if not c.get('header')]
    if not cells:
        raise HTTPException(409,'此表结构尚无法可靠导出。')
    buf=io.StringIO(newline='')
    writer=csv.writer(buf)
    clean=lambda text: "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
    if any(c.get('header') for c in detail['cells']):
        writer.writerow([clean(c['column_label']) for c in cells if c['row']==0])
    for row in sorted({c['row'] for c in cells}):
        writer.writerow([clean(c['value']) for c in sorted(cells,key=lambda c:c['column']) if c['row']==row])
    return Response('\ufeff'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="paperloop-table.csv"'})


class DetailAnchor(BaseModel):
    object_id:str=Field(min_length=1,max_length=180)
    kind:str
    item_id:str=Field(min_length=1,max_length=500)
    correction:dict|None=None


@app.post('/api/documents/{doc_id}/anchors')
def create_detail_anchor(doc_id:str,value:DetailAnchor):
    doc=store.get(doc_id)
    obj=reading_details.object_for(doc,value.object_id)
    if not obj:
        raise HTTPException(404,'图表对象不存在。')
    if value.correction is not None:
        if value.kind not in ('bar','cell','token','token_range') or set(value.correction)-{'series','category','metric','value','unit','note','text'}:
            raise HTTPException(400,'人工修正字段不受支持。')
        if any(not isinstance(x,str) or len(x)>200 for x in value.correction.values()):
            raise HTTPException(400,'人工修正内容过长。')
    try:
        return reading_details.save_anchor(doc,obj,value.kind,value.item_id,correction=value.correction)
    except ValueError as exc:
        raise HTTPException(400,str(exc))


@app.get('/api/documents/{doc_id}/anchors/{anchor_id}')
def detail_anchor(doc_id:str,anchor_id:str):
    store.get(doc_id)
    result=reading_details.get_anchor(doc_id,anchor_id)
    if not result:
        raise HTTPException(404,'局部选区不存在。')
    return result

@app.post('/api/documents/{doc_id}/research')
def research_paper(doc_id:str):
    return research.start(doc_id)


class LookupRequest(BaseModel):
    kind:str
    query:str=Field(min_length=2,max_length=240)


@app.post('/api/documents/{doc_id}/lookup')
def lookup(doc_id:str,value:LookupRequest):
    return study.start_lookup(doc_id,value.kind,value.query)


class AgentGoal(BaseModel):
    goal:str=Field(min_length=3,max_length=2000)
    max_steps:int=Field(default=8,ge=3,le=16)
    allow_web:bool=True

class AgentControl(BaseModel):
    action:Literal['pause','resume','cancel']
    max_steps:int|None=Field(default=None,ge=3,le=16)

def owned_agent_run(doc_id,run_id):
    store.get(doc_id)
    run=store.agent_get(run_id)
    if run['doc_id']!=doc_id:
        raise HTTPException(404,'当前论文中没有这个研究任务。')
    return run

def public_agent_run(run):
    return {key:value for key,value in run.items() if key not in ('provider_fingerprint','execution_token')}

@app.get('/api/documents/{doc_id}/agent-capabilities')
def agent_capabilities(doc_id:str):
    doc=store.get(doc_id)
    return {'tools':agent_tools.capabilities(doc,True),
            'notice':'模型按目标选择工具，并根据返回证据决定下一步。联网补查目前使用 ChatGPT 搜索连接。'}

@app.get('/api/documents/{doc_id}/agent-runs')
def agent_runs(doc_id:str):
    store.get(doc_id)
    return [public_agent_run(run) for run in store.agent_list(doc_id)]

@app.post('/api/documents/{doc_id}/agent-runs')
def agent_start(doc_id:str,value:AgentGoal):
    return public_agent_run(agent_loop.start(doc_id,value.goal,max_steps=value.max_steps,allow_web=value.allow_web))

@app.get('/api/documents/{doc_id}/agent-runs/{run_id}')
def agent_run(doc_id:str,run_id:str):
    return public_agent_run(owned_agent_run(doc_id,run_id))

@app.post('/api/documents/{doc_id}/agent-runs/{run_id}/control')
def agent_control(doc_id:str,run_id:str,value:AgentControl):
    owned_agent_run(doc_id,run_id)
    return public_agent_run(agent_loop.control(doc_id,run_id,value.action,max_steps=value.max_steps))

@app.get('/api/documents/{doc_id}/agent-runs/{run_id}/export')
def agent_export(doc_id:str,run_id:str,format:str='html'):
    run=owned_agent_run(doc_id,run_id)
    if format not in ('html','md'):
        raise HTTPException(400,'请选择 HTML 或 Markdown 格式。')
    content=agent_report.html_report(run) if format=='html' else agent_report.markdown(run)
    return Response(content,media_type='text/html; charset=utf-8' if format=='html' else 'text/markdown; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="paperloop-agent-{run_id[:8]}.{format}"'})


class ComparisonRequest(BaseModel):
    document_ids:list[str]=Field(min_length=2,max_length=5)
    dimensions:list[str]=Field(min_length=1,max_length=9)


@app.get('/api/comparisons')
def comparisons():
    return study.all_comparisons()


@app.post('/api/comparisons')
def compare(value:ComparisonRequest):
    return study.create_comparison(value.document_ids,value.dimensions)


@app.get('/api/comparisons/{comparison_id}')
def comparison(comparison_id:str):
    return study.get_comparison(comparison_id)


class ComparisonQuestion(BaseModel):
    question:str=Field(min_length=1,max_length=3000)


@app.post('/api/comparisons/{comparison_id}/ask')
def comparison_ask(comparison_id:str,value:ComparisonQuestion):
    return study.ask_comparison(comparison_id,value.question)


@app.get('/api/comparisons/{comparison_id}/export')
def comparison_export(comparison_id:str):
    content=study.comparison_markdown(study.get_comparison(comparison_id))
    return Response(content,media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="paperloop-comparison-{comparison_id[:8]}.md"'})


@app.get('/api/documents/{doc_id}/reading-report')
def reading_report(doc_id:str,format:str='html'):
    doc=store.get(doc_id)
    if format not in ('html','md'):
        raise HTTPException(400,'支持 HTML 或 Markdown 报告。')
    content=study.reading_report_html(doc) if format=='html' else study.reading_report(doc)
    mime='text/html; charset=utf-8' if format=='html' else 'text/markdown; charset=utf-8'
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="paperloop-reading-report-{doc_id[:8]}.{format}"'})

@app.get('/api/documents/{doc_id}/pdf')
def pdf(doc_id:str):
    store.get(doc_id)
    return FileResponse(store.DATA/(doc_id+'.pdf'),media_type='application/pdf')

@app.get('/api/documents/{doc_id}/pages/{page_no}/image')
def image(doc_id:str,page_no:int):
    doc=store.get(doc_id)
    if page_no<1 or page_no>doc['page_count']:
        raise HTTPException(404,'页面不存在。')
    return Response(parser.png_bytes(store.DATA/(doc_id+'.pdf'),page_no),media_type='image/png')

class Action(BaseModel):
    action:str
    block_id:str|None=None
    page:int|None=None

@app.post('/api/documents/{doc_id}/task')
def task(doc_id:str,value:Action):
    if value.action not in ('pause','resume','retry','ocr'):
        raise HTTPException(400,'未知操作。')
    def update(doc):
        if value.action=='pause':
            doc['status']='paused'
            doc['message']='已暂停；正在进行的单次请求结束后停止。'
        else:
            if worker.is_busy(doc_id):
                raise HTTPException(409,'正在结束当前请求，请稍后继续。')
            if doc['status'] in worker.ACTIVE:
                raise HTTPException(409,'任务正在执行，请等待或先暂停。')
            if value.action=='ocr':
                if not value.page or value.page<1 or value.page>doc['page_count']:
                    raise HTTPException(400,'请选择有效页面。')
                doc['pages']=[{**p,'status':'failed','force_ocr':True} if p['number']==value.page else p for p in doc['pages']]
            if value.action=='retry':
                for b in doc['blocks']:
                    if (value.block_id and b['id']==value.block_id) or (not value.block_id and b['status']=='failed'):
                        b.update(status='pending',attempts=0,error='')
            doc['status']='queued'
            doc['message']='等待继续处理'
        store.event(doc,doc['message'])
    with worker.CONTROL_LOCK:
        result=store.change(doc_id,update)
    worker.WAKE.set()
    return result

class Reading(BaseModel):
    page:int=Field(ge=1)

@app.put('/api/documents/{doc_id}/reading')
def reading(doc_id:str,value:Reading):
    def update(doc):
        if value.page>doc['page_count']:
            raise HTTPException(400,'页面超出范围。')
        doc['reading_page']=value.page
    store.change(doc_id,update)
    return {'ok':True}

class Note(BaseModel):
    text:str=Field(min_length=1,max_length=20000)
    block_id:str|None=None
    page:int|None=Field(default=None,ge=1)
    anchor_id:str|None=Field(default=None,max_length=160)
    box:list[float]|None=None
    boxes:list[list[float]]=Field(default_factory=list,max_length=200)
    quote:str=Field(default='',max_length=20000)
    color:Literal['yellow','green','blue','pink']='yellow'

@app.post('/api/documents/{doc_id}/notes')
def add_note(doc_id:str,value:Note):
    if not value.text.strip():
        raise HTTPException(400,'批注内容不能为空。')
    if value.boxes and (not value.page or any(not valid_box(box) for box in value.boxes)):
        raise HTTPException(400,'无效的文字高亮区域。')
    note={'id':uuid.uuid4().hex,'created_at':time.time(),**value.model_dump()}
    def update(doc):
        if value.anchor_id:
            anchor=reading_details.get_anchor(doc_id,value.anchor_id) if value.anchor_id.startswith('detail-') else None
            if anchor:
                note['page']=anchor['page']
                note['visual_box']=anchor['bbox']
            elif re.fullmatch(r'crop-[0-9a-fA-F]{8,16}',value.anchor_id) and value.page and valid_box(value.box):
                note['visual_box']=value.box
            else:
                obj=reading_details.object_for(doc,value.anchor_id)
                if not obj:
                    raise HTTPException(400,'引用选区不存在。')
                note['page']=obj['page']
                note['visual_box']=obj['bbox']
        elif value.box is not None:
            if not value.page or not valid_box(value.box):
                raise HTTPException(400,'无效的笔记图像选区。')
            note['visual_box']=value.box
        if value.page is not None and note['page']!=value.page:
            raise HTTPException(400,'批注选区与引用页码不一致。')
        block=next((b for b in doc['blocks'] if b['id']==value.block_id),None)
        if value.block_id:
            if block is None:
                raise HTTPException(400,'引用段落不存在。')
            if (note.get('visual_box') or value.boxes) and block['page']!=note['page']:
                raise HTTPException(400,'批注选区与段落不在同一页。')
            note['page']=block['page']
        if note['page'] and note['page']>doc['page_count']:
            raise HTTPException(400,'引用页码超出范围。')
        doc['notes'].append(note)
    store.change(doc_id,update)
    return note

class NoteEdit(BaseModel):
    text:str=Field(min_length=1,max_length=20000)
    color:Literal['yellow','green','blue','pink']='yellow'

@app.put('/api/documents/{doc_id}/notes/{note_id}')
def edit_note(doc_id:str,note_id:str,value:NoteEdit):
    if not value.text.strip():
        raise HTTPException(400,'批注内容不能为空。')
    def update(doc):
        note=next((item for item in doc['notes'] if item['id']==note_id),None)
        if note is None:
            raise HTTPException(404,'批注不存在。')
        note.update(text=value.text,color=value.color,updated_at=time.time())
    doc=store.change(doc_id,update)
    return next(item for item in doc['notes'] if item['id']==note_id)

@app.delete('/api/documents/{doc_id}/notes/{note_id}')
def delete_note(doc_id:str,note_id:str):
    store.change(doc_id,lambda d:d.update(notes=[n for n in d['notes'] if n['id']!=note_id]))
    return {'ok':True}

class Term(BaseModel):
    source:str=Field(min_length=2,max_length=120)
    target:str=Field(min_length=1,max_length=120)

class Glossary(BaseModel):
    terms:list[Term]=Field(max_length=100)

@app.put('/api/documents/{doc_id}/glossary')
def glossary(doc_id:str,value:Glossary):
    def update(doc):
        if worker.is_busy(doc_id):
            raise HTTPException(409,'正在结束当前请求，请稍后修改术语。')
        if doc['status'] in worker.ACTIVE:
            raise HTTPException(409,'请等待任务完成后修改术语。')
        terms=[t.model_dump() for t in value.terms]
        old={t['source']:t['target'] for t in doc['glossary']}
        new={t['source']:t['target'] for t in terms}
        changed={s for s in old.keys()|new.keys() if old.get(s)!=new.get(s)}
        for block in doc['blocks']:
            if block['status']!='preserved' and any(s.lower() in block['text'].lower() for s in changed):
                block.update(status='pending',translation='',attempts=0,error='')
        doc.update(glossary=terms,glossary_ready=True,glossary_version=doc['glossary_version']+1,status='ready',message='术语已更新，点击继续翻译以更新相关段落。')
    with worker.CONTROL_LOCK:
        return store.change(doc_id,update)

def tokens(text):
    return set(re.findall(r'[a-zA-Z]{2,}|[\u4e00-\u9fff]{1,2}',text.lower()))

class Question(BaseModel):
    question:str=Field(min_length=1,max_length=3000)
    mode:str='question'
    scope:str='paper'
    block_id:str|None=None
    selection:str=Field(default='',max_length=5000)
    page:int|None=Field(default=None,ge=1)
    box:list[float]|None=None
    anchor_id:str|None=Field(default=None,max_length=160)


def valid_box(box):
    return (isinstance(box,list) and len(box)==4 and
            all(isinstance(v,(float,int)) and not isinstance(v,bool) and 0<=v<=1 for v in box) and
            box[0]<box[2] and box[1]<box[3])


def region_key(page,box):
    """Normalized PDF coordinates make crop conversations independent of zoom and DPI."""
    coords=','.join(f'{v:.4f}' for v in box)
    digest=hashlib.sha256(f'{page}|{coords}'.encode()).hexdigest()[:16]
    return f'region:{page}:{digest}'

def question_context(doc,value):
    """Keep follow-ups scoped to the same source, independently of the visible page."""
    if value.mode=='guide':
        value.scope='paper'
    if value.anchor_id and not re.fullmatch(r'[A-Za-z0-9._:-]+',value.anchor_id):
        raise HTTPException(400,'无效的选区标识。')
    if value.mode=='visual':
        value.scope='page'
        if not value.page or value.page>doc['page_count']:
            raise HTTPException(400,'请选择有效页面。')
        if value.box is not None and not valid_box(value.box):
            raise HTTPException(400,'无效的图像选区。')
        if value.anchor_id and value.anchor_id.startswith('crop-'):
            if not re.fullmatch(r'crop-[0-9a-fA-F]{8,16}',value.anchor_id):
                raise HTTPException(400,'无效的图像选区标识。')
            return f'region:{value.page}:{value.anchor_id}'
        if value.anchor_id:
            return 'object:'+value.anchor_id
        if value.box:
            return region_key(value.page,value.box)
        return 'page:'+str(value.page)
    target=next((b for b in doc['blocks'] if b['id']==value.block_id and b['kind']!='margin'),None)
    if value.scope in ('selection','section'):
        if not target:
            raise HTTPException(400,'请先选择一个有效段落。')
        value.page=target['page']
        if value.scope=='section':
            return 'section:'+str(target['section'])
        if value.anchor_id:
            return f'selection:{target["id"]}:{value.anchor_id}'
        if value.selection.strip():
            normalized=' '.join(value.selection.split())
            digest=hashlib.sha256(normalized.encode()).hexdigest()[:16]
            return f'selection:{target["id"]}:text-{digest}'
        return 'selection:'+target['id']
    if value.scope=='page':
        if not value.page or value.page>doc['page_count']:
            raise HTTPException(400,'请选择有效页面。')
        return 'page:'+str(value.page)
    return 'paper'

def evidence_for(doc,value):
    blocks=[b for b in doc['blocks'] if b['kind']!='margin']
    if value.scope=='selection':
        target=next((b for b in blocks if b['id']==value.block_id),None)
        if target is None:
            raise HTTPException(400,'请先选择一个段落。')
        i=blocks.index(target)
        return blocks[max(0,i-1):i+2]
    if value.scope=='page':
        return [b for b in blocks if b['page']==value.page]
    if value.scope=='section':
        target=next((b for b in blocks if b['id']==value.block_id),None)
        if target is None:
            raise HTTPException(400,'请先选择当前章节中的段落。')
        return [b for b in blocks if b['section']==target['section']]
    if value.mode=='guide':
        # Coverage of all chapters, rather than only the opening pages.
        if sum(len(b['text']) for b in blocks)<=40000:
            return blocks
        groups={}
        for b in blocks:
            groups.setdefault(b['section'],[]).append(b)
        chosen=[]
        for group in groups.values():
            chosen.extend(group[:2]+group[-2:])
        seen=set()
        return [b for b in chosen if not (b['id'] in seen or seen.add(b['id']))]
    query=tokens(value.question)
    # Query expansion provides cross-language retrieval when no Chinese translation exists yet.
    expanded=provider.complete(doc['id'],[{'role':'system','content':provider.GUARD+' 把检索问题扩展为中英文关键词，仅输出至多 20 个空格分隔的检索词。'},{'role':'user','content':value.question}],max_tokens=300)
    query |= tokens(expanded)
    scored=sorted(enumerate(blocks),key=lambda pair:len(query & tokens(pair[1]['text']+' '+pair[1]['translation'])),reverse=True)
    indices=set()
    for i,b in scored[:10]:
        indices.update(range(max(0,i-1),min(len(blocks),i+2)))
    return [blocks[i] for i in sorted(indices)]

@app.post('/api/documents/{doc_id}/ask')
def ask(doc_id:str,value:Question):
    if not CHAT_LOCK.acquire(blocking=False):
        raise HTTPException(409,'另一个问题正在处理，请稍后再试。')
    try:
        doc=store.get(doc_id)
        if not provider.configured(store.settings()):
            raise HTTPException(409,'请先连接 ChatGPT，再进行提问。')
        if value.scope not in ('paper','section','selection','page') or value.mode not in ('question','guide','explain','visual'):
            raise HTTPException(400,'不支持的问答范围。')
        context_key=question_context(doc,value)
        visual=value.mode=='visual'
        if value.box is not None and not valid_box(value.box):
            raise HTTPException(400,'无效的图像选区。')
        visual_object=None
        detail_anchor=None
        if visual and value.anchor_id:
            prior=next((c for c in reversed(doc['chats']) if c.get('context_key')==context_key and valid_box(c.get('visual_box'))),None)
            if value.anchor_id.startswith('crop-'):
                if value.box is None:
                    if not prior:
                        raise HTTPException(400,'请重新框选图像区域。')
                    value.box=list(prior['visual_box'])
                elif prior and any(abs(a-b)>.0005 for a,b in zip(value.box,prior['visual_box'])):
                    raise HTTPException(400,'选区与已有讨论不一致，请重新框选。')
            else:
                objects=layout_objects.get_layout(doc,store.DATA/(doc_id+'.pdf'))['objects']
                detail_anchor=reading_details.get_anchor(doc_id,value.anchor_id) if value.anchor_id.startswith('detail-') else None
                visual_object=next((item for item in objects if item['id']==(detail_anchor['parent_id'] if detail_anchor else value.anchor_id)),None)
                if not visual_object or visual_object['page']!=value.page:
                    raise HTTPException(400,'图表选区与页面不匹配，请重新选择。')
                # The first request uses the document's measured rectangle. A later
                # follow-up without a box reuses the saved region even after zoom.
                value.box=list(detail_anchor['bbox'] if detail_anchor else prior['visual_box'] if prior and value.box is None else visual_object['bbox'])
        if value.scope=='selection' and value.anchor_id and not value.selection:
            prior=next((c for c in reversed(doc['chats']) if c.get('context_key')==context_key and c.get('selected_text')),None)
            if prior:
                value.selection=prior['selected_text']
        chosen=evidence_for(doc,value)
        used=[]
        length=0
        for block in chosen:
            if length+len(block['text'])>42000:
                break
            used.append(block)
            length+=len(block['text'])
        if not used and not visual:
            raise HTTPException(409,'当前范围尚无可读文本，请先完成解析，或选择页面视觉解释。')
        instruction=provider.GUARD+' 只能根据提供的证据作答。每项事实必须引用支持它的原文。找不到依据时说明未找到，不得编造。区分作者声称、论文证据、辅助解释。输出 JSON {"answer":"中文回答","citations":[{"block_id":"原文段落 ID","quote":"该段落中逐字引用的原文"}],"supplement":"可选的背景解释，明确非论文结论"}。'
        if value.mode=='guide':
            instruction+=' 导读包含研究问题、已有方法不足、本文方法、主要结果、作者承认的局限。未提供的内容注明无法判断。'
        instruction+=' 不要把内部 block_id 写入 answer 正文；引用位置由 citations 字段提供。'
        if visual:
            instruction+=' 对指定图像区域可见内容进行解释，结合图注和页面文本说明；看不清的数字、单位和符号请明确说明。图像证据会单独链接原页。'
            if detail_anchor:
                instruction+=' 局部对象可能仅是几何候选。人工修正与图例映射都须和图像核对；未经确认的估读不能表述为精确实验数据。空白单元格只说原表留空。'
        previous=[c for c in doc['chats'] if c.get('context_key','paper')==context_key][-4:]
        content={'task':value.mode,'question':value.question,'selected_text':value.selection,'focus_block_id':value.block_id if value.scope in ('selection','section') else None,'focus_region':{'page':value.page,'box':value.box,'anchor_id':value.anchor_id,'label':visual_object['label'] if visual_object else None,'caption':visual_object['caption'] if visual_object else None,'local_detail':{'kind':detail_anchor['kind'],'data':detail_anchor['data'],'correction':detail_anchor.get('correction',{})} if detail_anchor else None} if visual else None,'context':context_key,'evidence':[{'block_id':b['id'],'page':b['page'],'text':b['text']} for b in used],'previous_turns':[{'question':c['question'],'answer':c['answer']} for c in previous]}
        if value.scope=='selection':
            instruction+=' 回答重点是 selected_text 指定文字（若有），否则是 focus_block_id 指定段落；相邻段落仅为背景。延续同一选区 previous_turns 中的追问，指代须结合这些上下文。解释时按通俗含义、术语/符号、在论文中的作用组织；不可编造原文没有的信息。'
        user_content=json.dumps(content,ensure_ascii=False)
        if visual:
            data=base64.b64encode(parser.png_bytes(store.DATA/(doc_id+'.pdf'),value.page,value.box)).decode()
            user_content=[{'type':'text','text':user_content},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+data}}]
            if detail_anchor and visual_object:
                context_image=base64.b64encode(parser.png_bytes(store.DATA/(doc_id+'.pdf'),value.page,visual_object['bbox'])).decode()
                user_content += [{'type':'text','text':'第二张图为局部所属整图，仅用于核对图例、坐标轴、单位和表头。'},
                                 {'type':'image_url','image_url':{'url':'data:image/png;base64,'+context_image}}]
        response=provider.json_response(provider.complete(doc_id,[{'role':'system','content':instruction},{'role':'user','content':user_content}],vision=visual,max_tokens=3500))
        if not isinstance(response,dict) or not isinstance(response.get('answer'),str) or not response['answer'].strip() or not isinstance(response.get('citations',[]),list):
            raise provider.ProviderError('回答格式错误，请重试。')
        citations=[]
        for ref in response.get('citations',[])[:30]:
            if not isinstance(ref,dict):
                continue
            b=next((b for b in used if b['id']==ref.get('block_id')),None)
            quote=ref.get('quote','')
            if b and isinstance(quote,str) and quote.strip() and ' '.join(quote.split()) in ' '.join(b['text'].split()):
                citations.append({'block_id':b['id'],'page':b['page'],'quote':quote})
        answer=response['answer']
        for index,citation in enumerate(citations,1):
            answer=answer.replace('['+citation['block_id']+']',f'【原文 {index}】')
        if not citations and not visual:
            answer='当前证据中未获得可核对的引用，无法给出可靠结论。请缩小到某一段落或换一种问法。'
        partial=len(used)<len([b for b in doc['blocks'] if b['kind']!='margin']) or len(doc['pages'])<doc['page_count'] or any(p['warning'] for p in doc['pages'])
        supplement=response.get('supplement','')
        chat={'id':uuid.uuid4().hex,'created_at':time.time(),'question':value.question,'answer':answer,'supplement':supplement if isinstance(supplement,str) and (citations or visual) else '', 'citations':citations,'coverage':f'本次参考 {len(used)} 个文本块'+('；并非完整无缺失全文。' if partial else '。'),'visual_page':value.page if visual else None,'visual_box':list(value.box) if visual and value.box else None}
        chat.update(context_key=context_key,scope=value.scope,block_id=value.block_id if value.scope in ('selection','section') else None,page=value.page if value.scope!='paper' else None,anchor_id=value.anchor_id,selected_text=value.selection if value.scope=='selection' else '')
        if detail_anchor:
            chat['detail_snapshot']=detail_anchor
            chat['parent_visual_box']=visual_object['bbox']
        def save_chat(current):
            live_ids={b['id'] for b in current['blocks']}
            for citation in chat['citations']:
                if citation['block_id'] not in live_ids:
                    citation['stale']=True
            current['chats'].append(chat)
        store.change(doc_id,save_chat)
        return chat
    finally:
        CHAT_LOCK.release()

@app.get('/api/documents/{doc_id}/export')
def export(doc_id:str,format:str='html'):
    doc=store.get(doc_id)
    if format not in ('html','md'):
        raise HTTPException(400,'支持 HTML 或 Markdown 导出。')
    warning=f"状态：{doc['status']}。未完成、识别待检查的内容已标注。公式、页边文字与参考文献可能保留原文。"
    md=[f'# {doc["title"]}',warning]
    parts=[f'<h1>{html.escape(doc["title"])}</h1><p class="notice">{html.escape(warning)}</p>']
    for p in doc['pages']:
        if p['warning']:
            md.append(f'PDF 第 {p["number"]} 页提示：{p["warning"]}')
            parts.append(f'<p class="notice">PDF 第 {p["number"]} 页：{html.escape(p["warning"])}</p>')
    for b in doc['blocks']:
        status={'done':'已翻译','preserved':'保留原文','pending':'未翻译','translating':'翻译中','failed':'翻译失败'}.get(b['status'],b['status'])
        label=f'PDF 第 {b["page"]} 页 · {status}'
        zh=b['translation'] or ('【保留原文】' if b['status']=='preserved' else '【未完成】'+b.get('error',''))
        md.extend([f'\n## {label}',b['text'],zh])
        parts.append(f'<section id="{html.escape(b["id"])}"><small>{html.escape(label)}</small><div class="pair"><p>{html.escape(b["text"])}</p><p>{html.escape(zh)}</p></div></section>')
    md.append('\n# 我的笔记')
    parts.append('<h2>我的笔记</h2>')
    for note in doc['notes']:
        md.append(f'\nPDF 第 {note.get("page") or "未指定"} 页\n{note["text"]}')
        parts.append(f'<article><small>PDF 第 {note.get("page") or "未指定"} 页</small><p>{html.escape(note["text"])}</p></article>')
    if format=='md':
        content='\n\n'.join(md)
        mime='text/markdown; charset=utf-8'
    else:
        content='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PaperLoop 阅读导出</title><style>body{max-width:1100px;margin:40px auto;padding:24px;font:16px/1.8 system-ui;color:#263b32}section,article{border-bottom:1px solid #ddd;padding:16px 0;break-inside:avoid}.pair{display:grid;grid-template-columns:1fr 1fr;gap:32px}p{white-space:pre-wrap;overflow-wrap:anywhere}.notice{background:#fff3d6;padding:16px}small{color:#64746b}@media(max-width:700px){.pair{display:block}}@media print{body{margin:0;padding:0;font-size:11pt}}</style><body>'+''.join(parts)+'</body></html>'
        mime='text/html; charset=utf-8'
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="paperloop-{doc_id[:8]}.{format}"'})

if (store.ROOT/'dist').exists():
    app.mount('/',StaticFiles(directory=store.ROOT/'dist',html=True),name='frontend')
