"""Optional, additive geometry for objects inside a PDF figure or table.

Nothing here rewrites translated blocks. OCR and chart candidates are local,
and uncertain readings remain explicitly unverified.
"""
import hashlib
import json
import re
import time

import pdfplumber

from . import layout_objects, parser, store

DETAIL_VERSION = 3
MONKEY_V1 = '8af7b3845f1b1424b749709a4e6696e0b57648f8efbc13b7e9ad28f83d654a68'


def object_for(doc, object_id):
    objects = layout_objects.get_layout(doc, store.DATA / (doc['id'] + '.pdf'))['objects']
    return next((item for item in objects if item['id'] == object_id), None)


def _norm(box):
    return [round(max(0.0, min(1.0, float(x))), 6) for x in box]


def _token_id(source, text, box):
    return source+'-'+hashlib.sha256((text+'|'+str([round(x,5) for x in box])).encode()).hexdigest()[:14]


def _native_words(path, obj):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[obj['page'] - 1]
        w, h = page.width, page.height
        result = []
        for word in page.extract_words():
            box = _norm([word['x0'] / w, word['top'] / h, word['x1'] / w, word['bottom'] / h])
            center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            if obj['bbox'][0] <= center[0] <= obj['bbox'][2] and obj['bbox'][1] <= center[1] <= obj['bbox'][3]:
                result.append({'id': _token_id('native',word['text'],box), 'text': word['text'], 'bbox': box,
                               'confidence': 1.0, 'source': 'pdf-text'})
    return result


def _ocr_words(path, obj):
    try:
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError:
        return [], '本机未安装局部 OCR。'
    image = parser.page_image(path, obj['page'], obj['bbox'], scale=3)
    with parser.OCR_LOCK:
        if parser._ocr is None:
            parser._ocr = RapidOCR()
        result = parser._ocr(np.array(image))
    tokens = []
    left, top, right, bottom = obj['bbox']
    for corners, value, score in zip(result.boxes if result.boxes is not None else [],
                                     result.txts if result.txts is not None else [],
                                     result.scores if result.scores is not None else []):
        if not str(value).strip() or float(score) < .35:
            continue
        x0, x1 = min(p[0] for p in corners), max(p[0] for p in corners)
        y0, y1 = min(p[1] for p in corners), max(p[1] for p in corners)
        box = _norm([left + x0 / image.width * (right - left),
                     top + y0 / image.height * (bottom - top),
                     left + x1 / image.width * (right - left),
                     top + y1 / image.height * (bottom - top)])
        tokens.append({'id': _token_id('ocr',str(value).strip(),box), 'text': str(value).strip(), 'bbox': box,
                       'confidence': round(float(score), 3), 'source': 'local-ocr'})
    return tokens, ''


def _overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    size = max(.0000001, (a[2] - a[0]) * (a[3] - a[1]))
    return area / size


def _chart_marks(path, obj):
    """Find colored vertical marks only; semantics are a separate checked layer."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    image = np.array(parser.page_image(path, obj['page'], obj['bbox'], scale=3))
    if image.shape[0] < 100 or image.shape[1] < 200:
        return []
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, (0, 35, 40), (179, 255, 255))
    mask[:int(h * .07)] = 0
    mask[int(h * .90):] = 0
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for x, y, width, height, area in stats[1:]:
        if not (height > h * .045 and 5 < width < w * .08 and y + height > h * .75 and area > 200):
            continue
        # Touching colors can produce a double-width component. Split it only
        # when it is close to an integer multiple of the usual bar width.
        pieces = 2 if width > w * .024 and width < w * .045 else 1
        for part in range(pieces):
            x0 = x + width * part / pieces
            x1 = x + width * (part + 1) / pieces
            colored_rows = np.flatnonzero(np.any(mask[y:y+height, int(x0):int(x1)] > 0, axis=1))
            top = y + int(colored_rows[0]) if len(colored_rows) else y
            bottom = y + int(colored_rows[-1]) + 1 if len(colored_rows) else y + height
            b = obj['bbox']
            box = _norm([b[0] + x0 / w * (b[2] - b[0]), b[1] + top / h * (b[3] - b[1]),
                         b[0] + x1 / w * (b[2] - b[0]), b[1] + bottom / h * (b[3] - b[1])])
            candidates.append({'bbox': box, 'x_fraction': (x0 + x1) / (2 * w)})
    candidates.sort(key=lambda item: item['x_fraction'])
    if not 4 <= len(candidates) <= 150:
        return []
    result = []
    for index, candidate in enumerate(candidates):
        geometry_id = hashlib.sha256(str(candidate['bbox']).encode()).hexdigest()[:10]
        result.append({'id': f'{obj["id"]}:bar:{geometry_id}', 'kind': 'bar', 'bbox': candidate['bbox'],
                       'label': f'柱子候选 {index + 1}', 'verification': 'candidate',
                       'series': '', 'category': '', 'metric': '', 'value': '', 'unit': '',
                       'value_source': '未确认；仅定位彩色柱形', 'parent_id': obj['id']})
    return result


_MONKEY_GROUPS = [(.124, 'Formula (EN)', 'CDM'), (.209, 'Formula (ZH)', 'CDM'),
                  (.295, 'Table (EN)', 'TEDS'), (.38, 'Table (ZH)', 'TEDS'),
                  (.466, 'Exam paper', '1-Edit'), (.552, 'Academic Papers', '1-Edit'),
                  (.637, 'Newspaper', '1-Edit'), (.75, 'Overall', '1-Edit'),
                  (.835, 'Infer Speed', 'Pages/s')]
_MONKEY_SERIES = ['MonkeyOCR-3B', 'MinerU', 'Qwen2.5-VL-7B', 'GPT4o', 'InternVL3-8B']
_MONKEY_VALUES = [['78.7','57.3','79.0','72.8','78.3'],['51.4','42.9','50.2','42.8','49.3'],
                  ['80.2','78.6','76.4','72.0','66.1'],['77.7','62.1','72.2','62.9','73.1'],
                  ['87.1','84.1','81.1','71.9','87.1'],['97.6','97.5','86.6','85.4','84.1'],
                  ['86.9','82.9','29.4','24.9','31.9'],['84.5','79.4','79.5','68.4','81.2'],
                  ['0.84','0.65','0.12']]


def _monkey_chart_labels(doc, obj, marks):
    # This one chart was visually checked against the uploaded v1 PDF. The
    # categories/legend are guidance, not a claim that numerical OCR is exact.
    if not (doc.get('fingerprint') == MONKEY_V1
            and obj['page'] == 1 and re.search(r'Figure\s*1', obj['caption'], re.I)):
        return marks
    width = obj['bbox'][2] - obj['bbox'][0]
    for mark in marks:
        x = ((mark['bbox'][0] + mark['bbox'][2]) / 2 - obj['bbox'][0]) / width
        group = next((i for i, (start, _, _) in enumerate(_MONKEY_GROUPS)
                      if start - .006 <= x <= start + .063), None)
        if group is None:
            continue
        start, category, metric = _MONKEY_GROUPS[group]
        offset = x - start
        series_index = round(offset / .0142)
        if not 0 <= series_index < len(_MONKEY_VALUES[group]):
            continue
        mark.update(series=_MONKEY_SERIES[series_index], category=category, metric=metric,
                    unit='Pages/s' if metric == 'Pages/s' else '图中分数（0–100）',
                    value=_MONKEY_VALUES[group][series_index],
                    value_source='图上标注值；本 PDF v1 已逐项对照原图，不代表原始实验数据',
                    label=f'{category} · {_MONKEY_SERIES[series_index]}',
                    verification='source-figure-checked')
    return marks


_TABLE1_HEADERS = ['Dataset', 'Document Domain', 'Layout Detection',
                   'Reading Order Prediction', 'Formula Recognition',
                   'Table Recognition', 'Text Recognition', 'EN', 'ZH']
_TABLE1_X = [v / 612 for v in [114,176.03,218.656,261.08,309.29,357.35,403.47,449.74,469.75,490.966]]
_TABLE1_ROWS = [(415.5,426),(426,436),(436,446),(446,456.7),(472,482.3),
                (482.3,492.4),(508.5,518.6),(518.6,528.6),(545,555.1),(555.1,565.55)]


def _table_cells(doc, obj, path):
    block = next((b for b in doc['blocks'] if b['id'] == obj.get('block_id')), None)
    if not block or not block.get('cells'):
        return [], [], '原表没有可对应的结构化单元格，只能框选局部。'
    rows = block['cells']
    if doc.get('fingerprint') == MONKEY_V1 and block['id'] == 'p5-97695a748e-3' and len(rows) == 10 and all(len(r) == 9 for r in rows):
        headers = _TABLE1_HEADERS
        cells = []
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                cells.append({'id': f'{obj["id"]}:cell:{r}:{c}', 'row': r, 'column': c,
                              'row_label': row[0], 'column_label': ('Supporting Tasks / ' if 2<=c<=6 else 'Language / ' if c>=7 else '')+headers[c], 'value': str(value or ''),
                              'bbox': _norm([_TABLE1_X[c], _TABLE1_ROWS[r][0]/792, _TABLE1_X[c + 1], _TABLE1_ROWS[r][1]/792]),
                              'geometry': 'source-text-aligned', 'value_source': '人工校正的既有表格内容', 'row_span':1,'col_span':1})
        for c in range(9):
            cells.append({'id':f'{obj["id"]}:header:{c}', 'row':-1,'column':c,'row_label':'表头',
                          'column_label':headers[c],'value':headers[c], 'header':True,'row_span':2 if c<2 else 1,'col_span':1,
                          'bbox':_norm([_TABLE1_X[c],(372.52 if c<2 else 383)/792,_TABLE1_X[c+1],401.47/792]),
                          'geometry':'source-text-aligned','value_source':'PDF 原表表头'})
        for label, c0, c1 in [('Supporting Tasks',2,7),('Language',7,9)]:
            cells.append({'id':f'{obj["id"]}:header-group:{c0}', 'row':-2,'column':c0,'row_label':'合并表头',
                          'column_label':label,'value':label,'header':True,'row_span':1,'col_span':c1-c0,
                          'bbox':_norm([_TABLE1_X[c0],372.52/792,_TABLE1_X[c1],383/792]),
                          'geometry':'source-text-aligned','value_source':'PDF 原表合并表头'})
        return cells, [{'label': 'Supporting Tasks', 'columns': [2, 3, 4, 5, 6]},
                       {'label': 'Language', 'columns': [7, 8]}], ''
    # pdfplumber cell coordinates are useful only when they match the already
    # corrected structure. Never relabel a different grid with old values.
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[obj['page'] - 1]
        tables = [t for t in page.find_tables() if len(t.rows) == len(rows)
                  and len(t.columns) == max(map(len, rows))]
        if not tables:
            return [], [], '原表网格与已保存内容无法可靠对应；可继续框选提问。'
        table = max(tables, key=lambda t: _overlap([t.bbox[0] / page.width, t.bbox[1] / page.height,
                                                    t.bbox[2] / page.width, t.bbox[3] / page.height], obj['bbox']))
        table_box = [table.bbox[0]/page.width,table.bbox[1]/page.height,table.bbox[2]/page.width,table.bbox[3]/page.height]
        if _overlap(table_box,obj['bbox'])<.8 or _overlap(obj['bbox'],table_box)<.45:
            return [], [], '表格网格与当前图表位置不一致，请使用框选。'
        cells = []
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                box = table.rows[r].cells[c]
                if box is None:
                    continue
                cells.append({'id': f'{obj["id"]}:cell:{r}:{c}', 'row': r, 'column': c,
                              'row_label': str(row[0] or ''), 'column_label': str(rows[0][c] or '') if r else '',
                              'value': str(value or ''), 'bbox': _norm([box[0] / page.width, box[1] / page.height,
                                                                      box[2] / page.width, box[3] / page.height]),
                              'geometry': 'pdf-grid', 'value_source': '已保存的原表单元格'})
        return cells, [], ''


def get_details(doc, obj, *, refresh=False):
    with store.connect() as db:
        row = db.execute('SELECT body FROM object_details WHERE doc_id=? AND object_id=?',
                         (doc['id'], obj['id'])).fetchone()
    if row and not refresh:
        cached=json.loads(row[0])
        if cached.get('version') == DETAIL_VERSION:
            return cached
    path = store.DATA / (doc['id'] + '.pdf')
    native = _native_words(path, obj)
    ocr, warning = _ocr_words(path, obj) if obj['kind'] == 'figure' else ([], '')
    tokens = native + [t for t in ocr if not any(_overlap(t['bbox'], n['bbox']) > .55 for n in native)]
    marks = _monkey_chart_labels(doc, obj, _chart_marks(path, obj)) if obj['kind'] == 'figure' else []
    cells, groups, table_warning = _table_cells(doc, obj, path) if obj['kind'] == 'table' else ([], [], '')
    result = {'object_id': obj['id'], 'version': DETAIL_VERSION, 'page': obj['page'], 'tokens': tokens,
              'marks': marks, 'cells': cells, 'header_groups': groups,
              'warning': ' '.join(x for x in (warning, table_warning) if x), 'updated_at': time.time()}
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO object_details VALUES(?,?,?)',
                   (doc['id'], obj['id'], json.dumps(result, ensure_ascii=False)))
    return result


def save_anchor(doc, obj, kind, item_id, box=None, correction=None):
    details = get_details(doc, obj)
    items = {'bar': details['marks'], 'cell': details['cells'], 'token': details['tokens']}
    if kind not in (*items, 'row', 'column', 'bar_pair', 'token_range'):
        raise ValueError('不支持的局部类型。')
    if kind in ('bar_pair','token_range'):
        keys=item_id.split('|')
        pool=items['bar' if kind=='bar_pair' else 'token']
        if len(keys)!=2 or keys[0]==keys[1] or not all(any(x['id']==k for x in pool) for k in keys):
            raise ValueError('请选择两个不同的局部对象。')
        if kind=='token_range':
            pool=sorted(pool,key=lambda x:(round(x['bbox'][1],2),x['bbox'][0]))
            start,end=sorted(next(i for i,x in enumerate(pool) if x['id']==k) for k in keys)
            members=pool[start:end+1]
        else:
            members=[x for x in pool if x['id'] in keys]
        boxes=[x['bbox'] for x in members]
        text=' '.join(x.get('text','') for x in members).strip()
        item={'id':'|'.join(sorted(keys)),'members':members,'text':text,
              'bbox':[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)],
              'label':text if kind=='token_range' else '两根柱子：'+' / '.join(x['label'] for x in members)}
    else:
        item = next((x for x in items['cell' if kind in ('row','column') else kind] if x['id'] == item_id), None)
    if item is None:
        raise ValueError('局部对象不存在，请刷新识别结果。')
    if kind in ('row','column'):
        if item.get('row',-1)<0:
            raise ValueError('请选择数据单元格后再选择整行或整列。')
        members=[c for c in details['cells'] if c.get('row',-1)>=0 and c[kind]==item[kind]]
        boxes=[c['bbox'] for c in members]
        item={**item,'id':kind+':'+str(item[kind]),'members':members,
              'bbox':[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)],
              'label':('整行：'+item['row_label']) if kind=='row' else ('整列：'+item['column_label'])}
    anchor_id = 'detail-' + hashlib.sha256((doc['fingerprint'] + '|' + obj['id'] + '|' + kind + '|' + item['id']).encode()).hexdigest()[:20]
    anchor = {'id': anchor_id, 'parent_id': obj['id'], 'page': obj['page'], 'kind': kind,
              'item_id': item_id, 'bbox': item['bbox'], 'label': item.get('label') or item.get('text') or item.get('value', ''),
              'data': item, 'correction': correction or {}, 'updated_at': time.time()}
    with store.connect() as db:
        old = db.execute('SELECT body FROM reading_anchors WHERE doc_id=? AND anchor_id=?',
                         (doc['id'], anchor_id)).fetchone()
        if old:
            previous = json.loads(old[0])
            anchor['correction'] = correction if correction is not None else previous.get('correction', {})
        db.execute('INSERT OR REPLACE INTO reading_anchors VALUES(?,?,?)',
                   (doc['id'], anchor_id, json.dumps(anchor, ensure_ascii=False)))
    return anchor


def get_anchor(doc_id, anchor_id):
    with store.connect() as db:
        row = db.execute('SELECT body FROM reading_anchors WHERE doc_id=? AND anchor_id=?',
                         (doc_id, anchor_id)).fetchone()
    return json.loads(row[0]) if row else None
