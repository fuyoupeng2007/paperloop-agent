"""Portable report of a bounded agent run and its observed evidence."""
import html
import re
from . import research

STATUS={'running':'执行中','paused':'已暂停','completed':'已完成','needs_attention':'需要继续查证',
        'cancelled':'已停止','error':'执行遇到问题'}
ACTIONS={'search_paper':'检索论文','read_blocks':'阅读原文','inspect_figure':'查看图表',
         'web_search':'联网补证据','finish':'检查并形成结论','review':'核对结论'}


def _md(value):
    value=str(value or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return re.sub(r'([\\`*_\[\]])',r'\\\1',value)


def markdown(run):
    lines=['# PaperLoop 研究任务记录','',_md(run.get('goal')),'',
        '状态：'+STATUS.get(run.get('status'),_md(run.get('status'))),
        f"已执行 {run.get('steps_used',0)} / {run.get('max_steps',0)} 步；模型请求 {run.get('calls_used',0)} 次。",
        '', '## 结论与证据']
    for claim in run.get('claims',[]):
        lines+=['- '+_md(claim['text'])+'（证据：'+', '.join(_md(x) for x in claim['evidence_ids'])+'）']
    if not run.get('claims'):
        lines+=['尚无通过检查的结论。']
    lines+=['','## 局限与待解决问题']
    for value in [*run.get('limitations',[]),*run.get('open_questions',[])]:
        lines+=['- '+_md(value)]
    if run.get('error'):
        lines+=['- '+_md(run['error'])]
    lines+=['','## 执行记录']
    for step in run.get('steps',[]):
        lines+=['### '+str(step['index'])+'. '+ACTIONS.get(step.get('action'),_md(step.get('action'))),
            _md(step.get('reason')),_md(step.get('observation',{}).get('summary'))]
        if step.get('observation',{}).get('error'):
            lines+=[_md(step['observation']['error'])]
    lines+=['','## 证据记录']
    for item in run.get('evidence',[]):
        lines+=['### '+_md(item['id'])+' · '+_md(item.get('title'))]
        if item.get('page'):
            lines+=['PDF 第 '+str(item['page'])+' 页；段落 '+_md(item.get('block_id') or item.get('object_id'))]
        if item.get('url') and research.public_source(item['url']):
            url=item['url'].replace('(','%28').replace(')','%29')
            lines+=['[打开来源]('+url+')']
        lines+=['> '+_md(item.get('quote')).replace('\n','\n> ')]
    lines+=['','检查包括引用存在性与模型对证据的复核，不等同于人工事实认证。网页证据和视觉观察保留其来源类型。']
    return '\n\n'.join(lines)


def html_report(run):
    esc=lambda value:html.escape(str(value or ''),quote=True)
    para=lambda value:'<p>'+esc(value).replace('\n','<br>')+'</p>'
    parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>PaperLoop 研究任务记录</title><style>body{max-width:940px;margin:36px auto;padding:0 24px;color:#294631;font:15px/1.8 system-ui,sans-serif}h1,h2,h3{line-height:1.4}article{border:1px solid #dce7d8;border-radius:8px;padding:16px;margin:14px 0;break-inside:avoid}blockquote{margin:10px 0;padding:10px 16px;border-left:3px solid #d5b467;background:#faf7ee;white-space:pre-wrap;overflow-wrap:anywhere}a{color:#267044}small{color:#748776}p{overflow-wrap:anywhere}@media print{body{margin:0;max-width:none}a{color:inherit}}</style>',
        '<h1>PaperLoop 研究任务记录</h1>',para(run.get('goal')),
        '<p>状态：'+esc(STATUS.get(run.get('status'),run.get('status')))+' · '+str(run.get('steps_used',0))+' / '+str(run.get('max_steps',0))+' 步</p>',
        '<h2>结论与证据</h2>']
    for claim in run.get('claims',[]):
        parts+=['<article>',para(claim['text']),'<small>证据：'+
            '、'.join('<a href="#'+esc(key)+'">'+esc(key)+'</a>' for key in claim['evidence_ids'])+'</small></article>']
    if not run.get('claims'):
        parts+=[para('尚无通过检查的结论。')]
    parts+=['<h2>局限与待解决问题</h2>']
    parts+=[para(x) for x in [*run.get('limitations',[]),*run.get('open_questions',[]),run.get('error','')] if x]
    parts+=['<h2>执行记录</h2>']
    for step in run.get('steps',[]):
        parts+=['<article><h3>'+str(step['index'])+'. '+esc(ACTIONS.get(step.get('action'),step.get('action')))+'</h3>',
            para(step.get('reason')),para(step.get('observation',{}).get('summary')),
            para(step.get('observation',{}).get('error')),'</article>']
    parts+=['<h2>证据记录</h2>']
    for item in run.get('evidence',[]):
        parts+=['<article id="'+esc(item['id'])+'"><h3>'+esc(item.get('title'))+'</h3><small>'+esc(item['id'])+'</small>']
        if item.get('page'):
            parts+=[para('PDF 第 '+str(item['page'])+' 页')]
        if item.get('url') and research.public_source(item['url']):
            parts+=['<a href="'+esc(item['url'])+'" rel="noreferrer noopener">打开来源</a>']
        parts+=['<blockquote>'+esc(item.get('quote'))+'</blockquote></article>']
    parts+=[para('检查包括引用存在性与模型对证据的复核，不等同于人工事实认证。网页证据和视觉观察保留其来源类型。'),'</html>']
    return ''.join(parts)
