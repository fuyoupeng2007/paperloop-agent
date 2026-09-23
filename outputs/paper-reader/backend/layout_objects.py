"""Read-only figure/table hit regions derived from a PDF and existing blocks.

This layer deliberately does not rewrite parsed blocks. In particular, a
manually corrected table and its translation remain the document's source of
truth; the object only adds a clickable page rectangle.
"""

import re

import pdfplumber


CAPTION = re.compile(r'^\s*(Figure|Fig\.?|Table)\s*(\d+[A-Za-z]?)\s*[:.]?', re.I)


def _box(values):
    return [round(max(0.0, min(1.0, float(v))), 5) for v in values]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _near_image_body(images, caption_box, lower_limit, upper_limit):
    """Combine overlapping PDF image draws belonging to the same figure."""
    cap_top = caption_box[1]
    candidates = [b for b in images
                  if b[0] < b[2] and b[1] < b[3]
                  and b[2] - b[0] >= .035 and b[3] - b[1] >= .01
                  and b[1] >= lower_limit - .01 and b[1] < cap_top - .012
                  and b[3] <= cap_top + .08]
    seeds = [b for b in candidates if -.08 <= cap_top - b[3] <= .17]
    below = False
    if seeds:
        seed = min(seeds, key=lambda b: abs(cap_top - b[3]))
    else:
        # Some publishers put the caption above the figure.
        candidates = [b for b in images
                      if b[0] < b[2] and b[1] < b[3]
                      and b[2] - b[0] >= .035 and b[3] - b[1] >= .01
                      and b[1] >= caption_box[3] - .006
                      and b[3] <= upper_limit + .01]
        seeds = [b for b in candidates if -.006 <= b[1] - caption_box[3] <= .17]
        if not seeds:
            return None
        seed = min(seeds, key=lambda b: abs(b[1] - caption_box[3]))
        below = True
    group = [seed]
    rest = [b for b in candidates if b is not seed]
    changed = True
    while changed:
        changed = False
        current = _union(group)
        for b in list(rest):
            # Panels and repeated image records can overlap or nearly touch.
            if b[1] <= current[3] + .035 and b[3] >= current[1] - .035:
                group.append(b)
                rest.remove(b)
                changed = True
    result = _union(group)
    if below:
        result[1] = max(result[1], caption_box[3] + .003)
    else:
        result[3] = min(result[3], cap_top - .006)
    return _box(result) if result[3] - result[1] >= .025 else None


def _extend_with_embedded_chart(image_box, blocks, caption_box):
    """Some figures mix raster panels with vector chart lines and text."""
    nearby = [b['bbox'] for b in blocks
              if b.get('kind') == 'table'
              and b['bbox'][1] < caption_box[1]
              and b['bbox'][1] <= image_box[3] + .02
              and b['bbox'][3] >= image_box[1] - .02]
    if not nearby:
        return image_box
    box = _union([image_box] + nearby)
    box[3] = min(box[3], caption_box[1] - .006)
    return _box(box)


def _vector_figure_body(blocks, caption_box):
    """Use a chart-like parsed region when the PDF has no raster image."""
    cap_top = caption_box[1]
    chart_tables = [b for b in blocks if b.get('kind') == 'table'
                    and b['bbox'][1] < cap_top and -.005 <= cap_top - b['bbox'][3] <= .24]
    if chart_tables:
        main = min(chart_tables, key=lambda b: abs(cap_top - b['bbox'][3]))
        base = main['bbox']
        nearby = [b['bbox'] for b in blocks
                  if b.get('kind') in ('text', 'table')
                  and b['bbox'][1] >= base[1] - .07
                  and b['bbox'][1] < cap_top
                  and b['bbox'][3] >= base[1] - .07
                  and b['bbox'][0] <= base[2] + .08
                  and b['bbox'][2] >= base[0] - .08]
        box = _union([base] + nearby)
        box[3] = min(box[3], cap_top - .006)
        return _box(box), 'chart-region'
    # A vector-only diagram may have no extractable object at all. Keep the
    # region explicitly estimated so the viewer can still open the source crop.
    return _box([caption_box[0], max(.02, cap_top - .22), caption_box[2], cap_top - .008]), 'caption-region'


def _table_body(blocks, caption_box, lower_limit, upper_limit):
    cap_top = caption_box[1]
    above = [b for b in blocks if b.get('kind') == 'table'
             and b['bbox'][1] >= lower_limit - .01
             and b['bbox'][1] < cap_top and -.005 <= cap_top - b['bbox'][3] <= .45]
    below = [b for b in blocks if b.get('kind') == 'table'
             and b['bbox'][3] <= upper_limit + .01
             and b['bbox'][1] >= caption_box[3] - .005
             and -.005 <= b['bbox'][1] - caption_box[3] <= .45]
    matches = [(max(0, cap_top - b['bbox'][3]), b, False) for b in above]
    matches += [(max(0, b['bbox'][1] - caption_box[3]), b, True) for b in below]
    if not matches:
        return (_box([caption_box[0], max(.02, cap_top - .22), caption_box[2], cap_top - .008]),
                None, 'caption-region')
    _, main, caption_above = min(matches, key=lambda item: item[0])
    base = main['bbox']
    # pdfplumber can recognize grid lines yet omit row labels from its table
    # rectangle. Include text physically over the detected table.
    overlap = [b['bbox'] for b in blocks
               if b.get('kind') == 'text'
               and ((b['bbox'][1] >= caption_box[3] - .005 and b['bbox'][1] < upper_limit)
                    if caption_above else b['bbox'][1] < cap_top)
               and b['bbox'][3] >= base[1]
               and b['bbox'][1] <= base[3] + .02
               and b['bbox'][0] <= base[2] + .06
               and b['bbox'][2] >= base[0] - .06]
    box = _union([base] + overlap)
    if caption_above:
        box[1] = max(box[1], caption_box[3] + .001)
    else:
        box[3] = min(box[3], cap_top - .001)
    return _box(box), main['id'], 'table-block'


def derive_layout(doc, images_by_page):
    """Pure layout builder; all coordinates are normalized top-left boxes."""
    blocks_by_page = {}
    for block in doc.get('blocks', []):
        blocks_by_page.setdefault(block['page'], []).append(block)
    objects = []
    for page_no, blocks in blocks_by_page.items():
        captions = []
        for block in blocks:
            if block.get('kind') != 'caption':
                continue
            match = CAPTION.match(block.get('text', ''))
            if match:
                captions.append((block, match))
        captions.sort(key=lambda pair: pair[0]['bbox'][1])
        lower_limit = 0.0
        for index, (caption, match) in enumerate(captions):
            kind = 'table' if match.group(1).lower() == 'table' else 'figure'
            cap_box = caption['bbox']
            upper_limit = captions[index + 1][0]['bbox'][1] if index + 1 < len(captions) else 1.0
            if kind == 'figure':
                bbox = _near_image_body(images_by_page.get(page_no, []), cap_box,
                                        lower_limit, upper_limit)
                if bbox:
                    bbox = _extend_with_embedded_chart(bbox, blocks, cap_box)
                    source, confidence = 'pdf-image', 'detected'
                else:
                    bbox, source = _vector_figure_body(blocks, cap_box)
                    confidence = 'estimated'
                block_id = None
                label = '图 ' + match.group(2)
            else:
                bbox, block_id, source = _table_body(blocks, cap_box,
                                                     lower_limit, upper_limit)
                confidence = 'detected' if block_id else 'estimated'
                label = '表 ' + match.group(2)
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                obj = {'id': f'{kind}-{caption["id"]}', 'page': page_no,
                       'kind': kind, 'bbox': bbox, 'label': label,
                       'caption': caption['text'], 'caption_block_id': caption['id'],
                       'confidence': confidence, 'source': source}
                if block_id:
                    obj['block_id'] = block_id
                objects.append(obj)
            lower_limit = max(lower_limit, cap_box[3])
    return sorted(objects, key=lambda item: (item['page'], item['bbox'][1]))


def get_layout(doc, pdf_path):
    """Read one PDF pass and return hit regions without touching the database."""
    figure_pages = {b['page'] for b in doc.get('blocks', [])
                    if b.get('kind') == 'caption' and CAPTION.match(b.get('text', ''))
                    and CAPTION.match(b['text']).group(1).lower() != 'table'}
    images_by_page = {}
    if figure_pages:
        with pdfplumber.open(pdf_path) as pdf:
            for number in figure_pages:
                if not 1 <= number <= len(pdf.pages):
                    continue
                page = pdf.pages[number - 1]
                width, height = float(page.width), float(page.height)
                images_by_page[number] = [
                    [im['x0'] / width, im['top'] / height,
                     im['x1'] / width, im['bottom'] / height]
                    for im in page.images
                ]
    return {'objects': derive_layout(doc, images_by_page)}
