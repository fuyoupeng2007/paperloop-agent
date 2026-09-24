import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import secret_store

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('PAPERLOOP_DATA', str(ROOT / 'data'))).resolve()
DEFAULT_SETTINGS = {'provider':'codex', 'base_url': '', 'api_key': '', 'model': '', 'vision_model': '', 'call_limit': 500, 'input_price': 0, 'output_price': 0}
_SETTINGS_LOCK = threading.RLock()

def default_settings():
    result = DEFAULT_SETTINGS.copy()
    if os.environ.get('PAPERLOOP_DISTRIBUTED') == '1':
        result.update(provider='api', base_url='https://api.deepseek.com', model='deepseek-flash', vision_model='deepseek-flash')
    return result

@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / 'reader.db', timeout=30)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        with db:
            yield db
    finally:
        db.close()

def init():
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS object_details (doc_id TEXT NOT NULL, object_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(doc_id,object_id))')
        db.execute('CREATE TABLE IF NOT EXISTS reading_anchors (doc_id TEXT NOT NULL, anchor_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(doc_id,anchor_id))')
        db.execute('CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, body TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS agent_runs_document ON agent_runs(doc_id)')

def settings():
    with _SETTINGS_LOCK:
        with connect() as db:
            row = db.execute('SELECT body FROM settings WHERE id=1').fetchone()
        current = json.loads(row[0]) if row else {}
        if 'api_key' in current:
            legacy = current.pop('api_key') or ''
            if legacy:
                secret_store.save(DATA, legacy)
            with connect() as db:
                db.execute('UPDATE settings SET body=? WHERE id=1', (json.dumps(current, ensure_ascii=False),))
        return default_settings() | current | {'api_key': secret_store.load(DATA)}

def save_settings(value):
    with _SETTINGS_LOCK:
        current = settings()
        current.pop('api_key', None)
        update = dict(value)
        key = update.pop('api_key', None)
        if key is not None:
            secret_store.save(DATA, key)
        current.update(update)
        with connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(current, ensure_ascii=False),))

def get(doc_id):
    with connect() as db:
        row = db.execute('SELECT body FROM docs WHERE id=?', (doc_id,)).fetchone()
    if row is None:
        raise KeyError(doc_id)
    return json.loads(row[0])

def all_docs():
    with connect() as db:
        rows = db.execute('SELECT body FROM docs ORDER BY rowid DESC').fetchall()
    return [json.loads(row[0]) for row in rows]

def insert(doc):
    with connect() as db:
        db.execute('INSERT INTO docs VALUES(?,?,?)', (doc['id'], doc['fingerprint'], json.dumps(doc, ensure_ascii=False)))

def change(doc_id, operation):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM docs WHERE id=?', (doc_id,)).fetchone()
        if row is None:
            raise KeyError(doc_id)
        doc = json.loads(row[0])
        operation(doc)
        doc['updated_at'] = time.time()
        db.execute('UPDATE docs SET body=? WHERE id=?', (json.dumps(doc, ensure_ascii=False), doc_id))
    return doc

def event(doc, message):
    doc['events'] = (doc.get('events', []) + [{'time': time.time(), 'message': message}])[-100:]


def agent_get(run_id):
    with connect() as db:
        row=db.execute('SELECT body FROM agent_runs WHERE id=?',(run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return json.loads(row[0])


def agent_list(doc_id):
    with connect() as db:
        rows=db.execute('SELECT body FROM agent_runs WHERE doc_id=? ORDER BY rowid DESC LIMIT 50',(doc_id,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def agent_insert(run):
    with connect() as db:
        db.execute('INSERT INTO agent_runs VALUES(?,?,?)',(run['id'],run['doc_id'],json.dumps(run,ensure_ascii=False)))


def agent_change(run_id,operation):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT body FROM agent_runs WHERE id=?',(run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        run=json.loads(row[0])
        operation(run)
        run['updated_at']=time.time()
        db.execute('UPDATE agent_runs SET body=? WHERE id=?',(json.dumps(run,ensure_ascii=False),run_id))
    return run
