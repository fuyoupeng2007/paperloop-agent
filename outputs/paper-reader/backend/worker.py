import re
import threading
import time

from . import parser, provider, store

STOP = threading.Event()
WAKE = threading.Event()
ACTIVE = {'queued','parsing','translating'}
CONTROL_LOCK = threading.RLock()
BUSY_DOCS = set()

def is_busy(doc_id):
    with CONTROL_LOCK:
        return doc_id in BUSY_DOCS

def recover_block(block):
    if block['status']=='translating':
        block['status']='pending'
    if block['status']=='pending' and block['attempts']>=3:
        block.update(status='failed',error='请求中断且已达到自动重试上限，请手动重试。')

def assign_sections(blocks):
    section='正文'
    for block in blocks:
        if block['kind']=='heading':
            section=block['text']
        block['section']=section
        references=bool(re.fullmatch(r'(?:\d+[.\s]+)?(?:references|bibliography)\s*[:.]?',section.strip(),re.I))
        if block['kind'] in ('formula','margin','metadata') or references:
            block['status']='preserved'
        elif block['status']=='preserved':
            block.update(status='done' if block.get('translation') else 'pending',attempts=0,error='')

class Cancelled(Exception):
    pass

def check(doc_id):
    if STOP.is_set() or store.get(doc_id)['status'] == 'paused':
        raise Cancelled()

def set_state(doc_id,status,message=''):
    def update(doc):
        if doc['status']=='paused' and status!='queued':
            return
        doc['status']=status
        doc['message']=message
        if message:
            store.event(doc,message)
    store.change(doc_id,update)

def update_block(doc_id,block_id,**values):
    def update(doc):
        for block in doc['blocks']:
            if block['id']==block_id:
                block.update(values)
                if block['kind']=='title' and values.get('status')=='done':
                    doc['title_translation']=values.get('translation','')
                break
    store.change(doc_id,update)

def translate_batches(doc_id):
    """Amortize CLI startup over several paragraphs; commit each successful block independently."""
    while True:
        check(doc_id)
        doc=store.get(doc_id)
        batch=[]
        length=0
        for block in doc['blocks']:
            if block['status'] in ('done','preserved','failed'):
                continue
            if block['attempts']>=3:
                update_block(doc_id,block['id'],status='failed',error='已达到自动重试上限，请手动重试。')
                continue
            if batch and (len(batch)>=12 or length+len(block['text'])>6000):
                break
            batch.append(block)
            length+=len(block['text'])
        if not batch:
            return
        ids={b['id'] for b in batch}
        def begin(value):
            for b in value['blocks']:
                if b['id'] in ids:
                    b.update(status='translating',attempts=b['attempts']+1)
            if value['status']!='paused':
                value['message']=f'ChatGPT 正在翻译第 {batch[0]["page"]} 页，译文会陆续保存'
        store.change(doc_id,begin)
        try:
            translated,failed=provider.translate_batch(doc_id,batch,doc['glossary'])
        except (provider.BudgetError,provider.ConfigurationError):
            def release(value):
                for b in value['blocks']:
                    if b['id'] in ids:
                        b.update(status='pending',attempts=max(0,b['attempts']-1))
            store.change(doc_id,release)
            raise
        except Exception as exc:
            translated={}
            failed={b['id']:str(exc) if isinstance(exc,provider.ProviderError) else '本批翻译异常，请重试。' for b in batch}
        def save(value):
            for b in value['blocks']:
                if b['id'] in translated:
                    b.update(translation=translated[b['id']],status='done',error='')
                    if b['kind']=='title':
                        value['title_translation']=translated[b['id']]
                elif b['id'] in failed:
                    b.update(status='failed' if b['attempts']>=3 else 'pending',error=failed[b['id']])
        store.change(doc_id,save)

def process(doc_id):
    doc=store.get(doc_id)
    if doc['status']!='queued':
        return
    path=store.DATA / (doc_id+'.pdf')
    set_state(doc_id,'parsing','正在解析论文并建立原文定位')
    for page_no in range(1,doc['page_count']+1):
        check(doc_id)
        current=store.get(doc_id)
        old=next((p for p in current['pages'] if p['number']==page_no),None)
        if old and old['status']!='failed':
            continue
        parsed=None
        for attempt in range(3):
            check(doc_id)
            try:
                parsed=parser.parse_page(path,page_no,force_ocr=bool(old and old.get('force_ocr')))
                break
            except Exception:
                if attempt==2:
                    parsed={'number':page_no,'width':612,'height':792,'source':'failed','status':'failed','warning':'该页解析失败，原 PDF 可继续阅读；可单独重试。','blocks':[]}
        def save_page(value):
            blocks=parsed['blocks']
            replaced={b['id'] for b in value['blocks'] if b['page']==page_no}
            current_ids={b['id'] for b in blocks}
            for note in value['notes']:
                if note.get('block_id') in replaced-current_ids:
                    note['stale']=True
            for chat in value['chats']:
                for citation in chat.get('citations',[]):
                    if citation.get('block_id') in replaced-current_ids:
                        citation['stale']=True
            value['pages']=[p for p in value['pages'] if p['number']!=page_no]+[{k:v for k,v in parsed.items() if k!='blocks'}]
            value['pages'].sort(key=lambda p:p['number'])
            value['blocks']=[b for b in value['blocks'] if b['page']!=page_no]+blocks
            value['blocks'].sort(key=lambda b:(b['page'],b['order']))
            assign_sections(value['blocks'])
            if page_no==1:
                candidates=[b for b in blocks if b['kind']=='heading' and 12<len(b['text'])<200 and b['bbox'][1]<.55]
                first=parser.title_from_blocks(blocks) or (max(candidates,key=lambda b:b.get('size',0))['text'] if candidates else None)
                if first:
                    value['title']=first[:250]
            value['message']=f'已解析 {len(value["pages"])}/{value["page_count"]} 页'
        store.change(doc_id,save_page)
    check(doc_id)
    doc=store.get(doc_id)
    if not provider.configured(store.settings()):
        set_state(doc_id,'awaiting_model','解析已保存。连接模型后即可自动翻译。')
        return
    if not doc['blocks']:
        set_state(doc_id,'review','未提取到可翻译内容，请检查页面提示。')
        return
    set_state(doc_id,'translating','正在建立术语表并翻译')
    if not doc.get('glossary_ready'):
        terms=[]
        for attempt in range(3):
            check(doc_id)
            try:
                terms=provider.extract_glossary(doc_id,doc['blocks'])
                break
            except (provider.BudgetError,provider.ConfigurationError):
                raise
            except Exception:
                if attempt==2:
                    store.change(doc_id,lambda d:store.event(d,'自动术语提取失败，继续翻译；可手动添加术语。'))
        check(doc_id)
        store.change(doc_id,lambda d:d.update(glossary=terms,glossary_ready=True))
    if store.settings().get('provider')=='codex':
        translate_batches(doc_id)
    for i, original in enumerate(store.get(doc_id)['blocks']):
        check(doc_id)
        doc=store.get(doc_id)
        block=next(b for b in doc['blocks'] if b['id']==original['id'])
        if block['status'] in ('done','preserved','failed'):
            continue
        if block['attempts']>=3:
            update_block(doc_id,block['id'],status='failed',error='已达到自动重试上限，请手动重试。')
            continue
        context='\n'.join(b['text'] for b in doc['blocks'][max(0,i-1):i+2])[:6000]
        while block['attempts']<3:
            check(doc_id)
            block['attempts']+=1
            update_block(doc_id,block['id'],status='translating',attempts=block['attempts'])
            try:
                translated=provider.translate(doc_id,block,context,doc['glossary'])
                update_block(doc_id,block['id'],translation=translated,status='done',error='')
                break
            except (provider.BudgetError,provider.ConfigurationError):
                update_block(doc_id,block['id'],status='pending',attempts=block['attempts']-1)
                raise
            except Exception as exc:
                message=str(exc) if isinstance(exc,provider.ProviderError) else '翻译处理异常，请重试。'
                update_block(doc_id,block['id'],status='failed' if block['attempts']>=3 else 'pending',error=message)
                if STOP.wait(.5):
                    raise Cancelled()
    check(doc_id)
    doc=store.get(doc_id)
    issues=any(b['status'] not in ('done','preserved') for b in doc['blocks']) or any(p['warning'] or p['status']=='failed' for p in doc['pages'])
    set_state(doc_id,'review' if issues else 'completed','处理完成，有待核对内容。' if issues else '全文处理完成，可以开始精读。')

def run():
    while not STOP.is_set():
        with CONTROL_LOCK:
            queued=next((d for d in reversed(store.all_docs()) if d['status']=='queued'),None)
            if queued:
                BUSY_DOCS.add(queued['id'])
        if queued:
            doc_id=queued['id']
            try:
                process(doc_id)
            except Cancelled:
                pass
            except provider.ProviderError as exc:
                set_state(doc_id,'blocked',str(exc))
            except Exception:
                set_state(doc_id,'blocked','任务遇到异常，进度已保存。可重试继续。')
            finally:
                with CONTROL_LOCK:
                    BUSY_DOCS.discard(doc_id)
                    def settled(doc):
                        if doc['status']=='paused':
                            doc['message']='已暂停，进度已保存。'
                    store.change(doc_id,settled)
        else:
            WAKE.wait(1)
            WAKE.clear()

def start():
    STOP.clear()
    for doc in store.all_docs():
        if doc['status'] in ACTIVE or any(b['status']=='translating' for b in doc['blocks']):
            def recover(d):
                if d['status'] in ACTIVE:
                    d['status']='queued'
                for b in d['blocks']:
                    recover_block(b)
                store.event(d,'服务重新启动，继续未完成任务。')
            store.change(doc['id'],recover)
    thread=threading.Thread(target=run,name='paperloop-worker',daemon=True)
    thread.start()
    return thread
