"""Local parser adapter. Coordinates are normalized, top-left origin, page rotation applied."""
import hashlib
import io
import re
import statistics
import threading
import unicodedata

import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader

RENDER_LOCK = threading.Lock()
OCR_LOCK = threading.Lock()
_ocr = None
PARSER_VERSION = 'plumber-layout-v2'

def inspect_pdf(path):
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError('PDF 已加密，请上传未加密的副本。')
    count = len(reader.pages)
    if count < 1 or count > 100:
        raise ValueError('第一版支持 1–100 页 PDF。')
    title = str((reader.metadata or {}).get('/Title', '')).strip()
    return count, title[:250]

def page_image(path, page_no, box=None, scale=1.7):
    with RENDER_LOCK:
        pdf = pdfium.PdfDocument(path)
        page = pdf[page_no - 1]
        w, h = page.get_size()
        if w <= 0 or h <= 0 or w * h > 16_000_000:
            page.close()
            pdf.close()
            raise ValueError('页面尺寸超出处理范围。')
        scale = min(scale, (12_000_000 / (w*h)) ** .5)
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().copy()
        bitmap.close()
        page.close()
        pdf.close()
    if box:
        image = image.crop((int(box[0]*image.width), int(box[1]*image.height), int(box[2]*image.width), int(box[3]*image.height)))
    return image

def png_bytes(path, page_no, box=None):
    buffer = io.BytesIO()
    page_image(path, page_no, box).save(buffer, format='PNG')
    return buffer.getvalue()

def normalized(box, width, height):
    return [max(0, min(1, value / (width if i % 2 == 0 else height))) for i, value in enumerate(box)]

def classify(text, size, median):
    if re.match(r'^(Figure|Fig\.|Table)\s*\d', text, re.I):
        return 'caption'
    # A sentence beginning with "methods" or "results" is still body text.
    named_heading = re.fullmatch(r'(?:Abstract|Introduction|Related Work|Methods?|Methodology|Materials and Methods|Experiments?|Results?|Discussion|Conclusions?|References|Bibliography|Appendix|Acknowledg(?:e)?ments?)\s*[:.]?', text, re.I)
    numbered_heading = len(text) < 120 and re.fullmatch(r'\d+(?:\.\d+)*\.?\s+[A-Z][\w ,:&()/–—-]+', text) and not re.search(r'\b(?:University|Institute|Laboratory|Department)\b', text)
    if 4 <= len(text) < 300 and (size > median*1.35 or named_heading or numbered_heading):
        return 'heading'
    if len(text) < 350 and len(re.findall(r'[=∑∫∂≤≥∈∇±]', text)) >= 2:
        return 'formula'
    return 'text'

def word_rows(words):
    rows = []
    for word in sorted(words, key=lambda v: (round(v['top']/3), v['x0'])):
        if not rows or abs(word['top'] - rows[-1][0]['top']) > 4:
            rows.append([word])
        else:
            rows[-1].append(word)
    return rows


def group_words(words):
    lines = []
    rows = word_rows(words)
    for row in rows:
        row.sort(key=lambda v:v['x0'])
        lines.append({'text': ' '.join(v['text'] for v in row), 'x0': min(v['x0'] for v in row), 'x1': max(v['x1'] for v in row), 'top': min(v['top'] for v in row), 'bottom': max(v['bottom'] for v in row), 'size': statistics.median(v.get('size', v['bottom']-v['top']) for v in row)})
    return lines

def ordered_regions(words, width):
    # A whitespace gutter supported by many same-height line pairs indicates two columns.
    rows = word_rows(words)
    gaps = []
    for row in rows:
        row_words = sorted(row, key=lambda w:w['x0'])
        for index, (left, right) in enumerate(zip(row_words, row_words[1:])):
            if left['x1'] < width*.57 and right['x0'] > width*.43 and right['x0']-left['x1'] > 18:
                # Numeric tables and plot labels do not establish a body-text column.
                sides = (row_words[:index+1], row_words[index+1:])
                if all(sum(c.isalpha() for w in side for c in w['text']) >= 15 for side in sides):
                    gaps.append((left['x1']+right['x0'])/2)
    if len(gaps) < 5:
        return [words], False
    split = statistics.median(gaps)
    regions, left_band, right_band, full_band = [], [], [], []
    median = statistics.median(w.get('size', w['bottom']-w['top']) for w in words)
    def flush(*bands):
        for band in bands:
            if band:
                regions.append(list(band))
                band.clear()
    # Partition whole rows, once each; overlapping coordinate windows lost or
    # duplicated superscripts and broke full-width paragraphs into single lines.
    for row in rows:
        left = [w for w in row if (w['x0']+w['x1'])/2 < split]
        right = [w for w in row if (w['x0']+w['x1'])/2 >= split]
        crosses = any(w['x0'] < split < w['x1'] for w in row)
        narrow_gap = left and right and min(w['x0'] for w in right)-max(w['x1'] for w in left) < 18
        large_centered = left and right and statistics.median(w.get('size', median) for w in row) > median*1.25
        if crosses or narrow_gap or large_centered:
            flush(left_band, right_band)
            full_band.extend(row)
        else:
            flush(full_band)
            left_band.extend(left)
            right_band.extend(right)
    flush(full_band, left_band, right_band)
    return regions, True


def rotated_blocks(chars, width, height, tables=()):
    """Keep vertical labels intact and out of horizontal paragraphs."""
    groups = []
    for char in sorted(chars, key=lambda c: c['x0']):
        if char.get('upright', True) or any(t[0] <= (char['x0']+char['x1'])/2 <= t[2] and t[1] <= (char['top']+char['bottom'])/2 <= t[3] for t in tables):
            continue
        direction = 'btt' if char.get('matrix', (0, 1))[1] > 0 else 'ttb'
        match = next((g for g in groups if g[0] == direction and abs(g[1][0]['x0']-char['x0']) < 3), None)
        if match is None:
            groups.append((direction, [char]))
        else:
            match[1].append(char)
    raw = []
    for direction, group in groups:
        words = pdfplumber.utils.extract_words(group, line_dir_rotated='ltr', char_dir_rotated=direction, x_tolerance=2, y_tolerance=3)
        text = unicodedata.normalize('NFKC', ' '.join(w['text'] for w in words))
        box = [min(c['x0'] for c in group), min(c['top'] for c in group), max(c['x1'] for c in group), max(c['bottom'] for c in group)]
        margin = box[2] < width*.1 or box[0] > width*.9
        raw.append({'text': text, 'bbox': normalized(box, width, height), 'kind': 'margin' if margin else 'text', 'rotation': 90 if direction == 'btt' else 270})
    return raw


def merge_title_lines(raw, median, height):
    merged = []
    for block in raw:
        previous = merged[-1] if merged else None
        if previous and previous['kind'] == block['kind'] == 'heading' and min(previous.get('size', 0), block.get('size', 0)) > median*1.35 and block['bbox'][3] < .45:
            a, b = previous['bbox'], block['bbox']
            gap = (b[1]-a[3])*height
            aligned = abs((a[0]+a[2])-(b[0]+b[2])) < .08 or abs(a[0]-b[0]) < .02
            if 0 <= gap <= previous['size']*.6 and abs(previous['size']-block['size']) < 1 and aligned:
                previous['text'] += ' '+block['text']
                previous['bbox'] = [min(a[0],b[0]),a[1],max(a[2],b[2]),b[3]]
                continue
        merged.append(block)
    return merged


def title_from_blocks(blocks, page_height=None):
    """Return a complete first-page title, or an empty string when uncertain."""
    candidates = [b for b in blocks if b.get('page', 1) == 1 and b.get('kind') in ('title', 'heading') and b.get('bbox', [0,1,0,1])[1] < .5 and len(b.get('text', '')) >= 8 and b.get('size', 0) > 0 and not re.match(r'^(?:Abstract|Introduction|Related Work|References|Bibliography|arXiv:|Preprint|Copyright)\b|^\d{1,2}(?:\.\d+)*\.?\s+', b['text'], re.I)]
    if not candidates:
        return ''
    return max(candidates, key=lambda b: (b['size'], -b['bbox'][1]))['text'].strip()[:250]


def annotate_front_matter(raw):
    title = title_from_blocks(raw)
    main = next((b for b in raw if b['text'] == title), None)
    if not main:
        return raw
    main['kind'] = 'title'
    for block in raw:
        if block['kind'] != 'text' or not (main['bbox'][3] <= block['bbox'][1] < min(.45, main['bbox'][3]+.2)):
            continue
        text = block['text']
        affiliation = bool(re.search(r'\b(?:University|Institute|Laboratory|Department|School of|Office|Research Cent(?:er|re)|Corporation)\b|[\w.+-]+@[\w.-]+', text))
        # Recognize short name lists, without suppressing an opening body paragraph.
        clean = re.sub(r'[\d†‡*]', '', text)
        names = [name.split() for name in re.split(r',|\band\b', clean) if name.strip()]
        authors = bool(names) and all(2 <= len(name) <= 5 and all(part[:1].isupper() for part in name) for name in names)
        if len(text) < 450 and (affiliation or authors):
            block['kind'] = 'metadata'
    return raw

def parse_page(path, page_no, force_ocr=False):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_no-1]
        width, height = float(page.width), float(page.height)
        words = page.filter(lambda c: c.get('object_type') != 'char' or c.get('upright', True)).extract_words(extra_attrs=['size'], x_tolerance=2, y_tolerance=3)
        source, warning, raw, tables = 'text', '', [], []
        if force_ocr or sum(len(w['text']) for w in words)+sum(len(c['text']) for c in page.chars if not c.get('upright', True)) < 30:
            return ocr_page(path, page_no, width, height)
        try:
            for table in page.find_tables():
                cells = table.extract()
                if len(cells) < 2 or max((len(r) for r in cells), default=0) < 2:
                    continue
                box = table.bbox
                tables.append(box)
                raw.append({'text': '\n'.join(' | '.join(str(c or '') for c in row) for row in cells), 'bbox': normalized(box,width,height), 'kind': 'table', 'cells': cells})
        except Exception:
            warning = '表格结构识别未完成，请对照原页。'
        words = [w for w in words if not any(t[0] <= (w['x0']+w['x1'])/2 <= t[2] and t[1] <= (w['top']+w['bottom'])/2 <= t[3] for t in tables)]
        regions, two_column = ordered_regions(words, width)
        median = statistics.median([w['size'] for w in words]) if words else 10
        for region in regions:
            group = []
            def flush():
                if not group:
                    return
                text = unicodedata.normalize('NFKC', ' '.join(x['text'] for x in group))
                text = re.sub(r'([a-z])-\s+([a-z])', r'\1\2', text)
                box = [min(x['x0'] for x in group), min(x['top'] for x in group), max(x['x1'] for x in group), max(x['bottom'] for x in group)]
                kind = classify(text, max(x['size'] for x in group), median)
                if box[1] < height*.035 or box[3] > height*.968:
                    kind = 'margin'
                raw.append({'text':text, 'bbox':normalized(box,width,height),'kind':kind,'size':round(max(x['size'] for x in group),2)})
                group.clear()
            for line in group_words(region):
                heading = classify(line['text'], line['size'], median) == 'heading'
                if group and (heading or line['top']-group[-1]['bottom'] > median*.65 or abs(line['size']-group[-1]['size']) > 1.5 or sum(len(x['text']) for x in group)>1600):
                    flush()
                group.append(line)
                if heading:
                    flush()
            flush()
        # Table blocks are inserted at their corresponding visual column position.
        if tables:
            raw.sort(key=lambda b: ((0 if b['bbox'][0]<.5 else 1) if two_column else 0, b['bbox'][1]))
        if page_no == 1:
            raw = merge_title_lines(raw, median, height)
            raw = annotate_front_matter(raw)
        raw.extend(rotated_blocks(page.chars, width, height, tables))
        if any('\ufffd' in b['text'] or '(cid:' in b['text'] for b in raw):
            warning = (warning+' 文本含无法解码字符，请检查或使用 OCR。').strip()
        if sum(b['kind']=='heading' for b in raw)>8:
            warning = (warning+' 此页版式复杂，请核对阅读顺序。').strip()
        return finish(raw,page_no,width,height,source,warning)

def ocr_page(path, page_no, width, height):
    global _ocr
    try:
        from rapidocr import RapidOCR
        import numpy as np
        image = page_image(path,page_no,scale=2)
        with OCR_LOCK:
            if _ocr is None:
                _ocr = RapidOCR()
            result = _ocr(np.array(image))
        raw, scores = [], []
        for box, text, score in zip(result.boxes if result.boxes is not None else [], result.txts if result.txts is not None else [], result.scores if result.scores is not None else []):
            scores.append(float(score))
            raw.append({'text':text,'kind':'text','bbox':normalized([min(p[0] for p in box),min(p[1] for p in box),max(p[0] for p in box),max(p[1] for p in box)],image.width,image.height)})
        warning = 'OCR 识别结果，请核对公式、数字与阅读顺序。'
        if not raw:
            warning = '该页未识别到文字；可能为空白页或纯图片页，请查看原页。'
        elif min(scores) < .8:
            warning += ' 部分文字识别置信度低于 80%。'
        return finish(raw,page_no,width,height,'ocr',warning)
    except ImportError:
        return finish([],page_no,width,height,'failed','该页需要 OCR；请运行安装脚本安装本地 OCR 依赖后重试。')

def finish(raw,page_no,width,height,source,warning):
    blocks = []
    for i, item in enumerate(raw):
        digest = hashlib.sha256((item['text']+str(item['bbox'])).encode()).hexdigest()[:10]
        blocks.append({'id':f'p{page_no}-{digest}-{i}', 'page':page_no, 'order':i, 'translation':'', 'status':'preserved' if item['kind'] in ('formula','margin','metadata') else 'pending', 'attempts':0, 'error':'', 'section':'', **item})
    return {'number':page_no,'width':width,'height':height,'source':source,'warning':warning,'status':'done' if blocks else 'review','blocks':blocks}
