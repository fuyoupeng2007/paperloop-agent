import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {chromium} from '../outputs/paper-reader/node_modules/@playwright/test/index.mjs';

const base='http://127.0.0.1:8766',docId='f9091cd15d594b599bcf882010ec1479';
const edge='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const browser=await chromium.launch({headless:true,executablePath:existsSync(edge)?edge:undefined});
const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const shot=name=>page.screenshot({path:fileURLToPath(new URL(name,import.meta.url))});
const doc=async()=>{const response=await page.request.get(base+'/api/documents/'+docId);assert.equal(response.status(),200);return response.json();};
// This port serves only the disposable test copy. Clear only this script's
// own prior annotations so reruns do not create overlapping badge targets.
for(const note of (await doc()).notes.filter(n=>n.text.startsWith('浏览器验证：'))){
  const response=await page.request.delete(base+'/api/documents/'+docId+'/notes/'+note.id,{headers:{'X-PaperLoop':'1'}});
  assert.equal(response.status(),200);
}
const baseline=await doc();
async function openFigure(){await page.getByLabel('当前页').selectOption('1');await page.locator('.pdf-object-badge').filter({hasText:'图 1'}).first().dblclick();await rendered(1);}
async function rendered(zoom){await page.waitForFunction(expected=>{const c=document.querySelector('.figure-image-layer canvas');return c&&Math.abs(Number(c.dataset.renderZoom)-expected)<.001;},zoom,{timeout:20000});}
async function save(text,color='蓝色批注',method='POST'){
  const editor=page.getByRole('dialog',{name:method==='POST'?'添加批注':'编辑批注',exact:true});
  await editor.getByRole('textbox',{name:'批注内容'}).fill(text);
  await editor.getByRole('button',{name:color,exact:true}).click();
  const responsePromise=page.waitForResponse(r=>r.url().includes('/notes')&&r.request().method()===method);
  await editor.getByRole('button',{name:'保存批注',exact:true}).click();
  const response=await responsePromise;assert.equal(response.status(),200,await response.text());
  await editor.waitFor({state:'hidden'});
  return (await doc()).notes.findLast(n=>n.text===text);
}
try{
  await page.goto(base,{waitUntil:'networkidle'});
  await page.locator('.paper-card').filter({hasText:'MonkeyOCR'}).first().click();
  await openFigure();
  const initial=await page.locator('.figure-image-layer canvas').evaluate(c=>({width:c.width,height:c.height,css:c.getBoundingClientRect().width}));
  assert.ok(initial.width/initial.css>=1.99,JSON.stringify(initial));
  const zoomIn=page.locator('.figure-viewer button[title="放大"]'),zoomOut=page.locator('.figure-viewer button[title="缩小"]');
  for(let i=0;i<8;i++)await zoomIn.click();
  for(let i=0;i<3;i++)await zoomOut.click();
  await rendered(2);
  const zoomed=await page.locator('.figure-image-layer canvas').evaluate(c=>({width:c.width,height:c.height,css:c.getBoundingClientRect().width}));
  assert.ok(zoomed.width>initial.width*1.99,JSON.stringify({initial,zoomed}));
  assert.ok(zoomed.width/zoomed.css>=1.99);
  assert.ok(zoomed.width*zoomed.height<=20_000_000);
  await shot('figure-crisp-dpr2-200percent.png');
  await page.locator('.figure-viewer button[title="恢复大小"]').click();await rendered(1);
  await page.getByRole('button',{name:'批注整图',exact:true}).click();
  assert.equal(await page.locator('.figure-viewer').count(),0);
  const figureNote=await save('浏览器验证：整图批注，刷新后仍然可编辑。');
  assert.ok(figureNote.visual_box?.length===4);
  await page.locator('.pdf-annotation-badge[data-annotation-id="'+figureNote.id+'"]').click();
  const edited=await save('浏览器验证：整图批注已编辑。','粉色批注','PUT');
  assert.equal(edited.id,figureNote.id);assert.equal(edited.color,'pink');
  await openFigure();
  await page.locator('.figure-viewer .pdf-annotation-badge[data-annotation-id="'+figureNote.id+'"]').click();
  assert.equal(await page.locator('.figure-viewer').count(),0);
  await page.getByRole('button',{name:'关闭批注',exact:true}).click();
  await openFigure();
  await page.getByRole('button',{name:'框选局部',exact:true}).click();
  const layer=await page.locator('.figure-image-layer').boundingBox();
  await page.mouse.move(layer.x+layer.width*.6,layer.y+layer.height*.45);
  await page.mouse.down();await page.mouse.move(layer.x+layer.width*.78,layer.y+layer.height*.78,{steps:8});await page.mouse.up();
  await page.getByRole('button',{name:'批注选区',exact:true}).click();
  const cropNote=await save('浏览器验证：图中局部批注。','绿色批注');
  assert.ok(cropNote.visual_box[2]-cropNote.visual_box[0]<figureNote.visual_box[2]-figureNote.visual_box[0]);
  const selected=await page.evaluate(()=>{
    const span=[...document.querySelectorAll('.paper-page-wrap[data-pdf-page="1"] .textLayer span')].find(el=>el.textContent?.includes('MonkeyOCR'));
    if(!span?.firstChild)return false;
    const range=document.createRange();range.setStart(span.firstChild,0);range.setEnd(span.firstChild,9);
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    span.closest('.page-sheet').dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));return true;
  });
  assert.ok(selected);
  await page.getByRole('toolbar',{name:'选中文字操作'}).getByRole('button',{name:/批注|笔记/}).click();
  const textNote=await save('浏览器验证：精确文字高亮。','黄色批注');
  assert.ok(textNote.boxes?.length>0);assert.equal(textNote.quote,'MonkeyOCR');
  assert.ok(textNote.boxes[0][2]-textNote.boxes[0][0]<.3);
  await page.reload({waitUntil:'networkidle'});
  if(await page.locator('.paper-card').count())await page.locator('.paper-card').filter({hasText:'MonkeyOCR'}).first().click();
  await page.getByLabel('当前页').selectOption('1');
  for(const note of [figureNote,cropNote,textNote])await page.locator('.pdf-annotation-box[data-annotation-id="'+note.id+'"]').first().waitFor();
  await shot('figure-and-text-annotations-reloaded.png');
  await page.locator('.reader-toolbar').getByRole('button',{name:/^批注|^笔记/}).click();
  const card=page.locator('.note-card').filter({hasText:'浏览器验证：精确文字高亮。'});
  const removed=page.waitForResponse(r=>r.url().endsWith('/notes/'+textNote.id)&&r.request().method()==='DELETE');
  await card.getByTitle('删除笔记').click();assert.equal((await removed).status(),200);
  await page.locator('.pdf-annotation-box[data-annotation-id="'+textNote.id+'"]').waitFor({state:'hidden'});
  const after=await doc();assert.deepEqual(after.blocks,baseline.blocks);assert.deepEqual(after.notes.slice(0,baseline.notes.length),baseline.notes);assert.deepEqual(after.chats,baseline.chats);
  assert.equal(after.notes.length,baseline.notes.length+2);
  const dense=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:3});
  dense.on('pageerror',error=>errors.push(error.message));
  await dense.goto(base,{waitUntil:'networkidle'});
  await dense.locator('.paper-card').filter({hasText:'MonkeyOCR'}).first().click();
  await dense.getByLabel('当前页').selectOption('1');
  await dense.locator('.pdf-object-badge').filter({hasText:'图 1'}).first().dblclick();
  for(let i=0;i<15;i++)await dense.locator('.figure-viewer button[title="放大"]').click();
  await dense.waitForFunction(()=>Number(document.querySelector('.figure-image-layer canvas')?.dataset.renderZoom)===4,null,{timeout:20000});
  const capped=await dense.locator('.figure-image-layer canvas').evaluate(c=>({width:c.width,height:c.height,ratio:Number(c.dataset.renderRatio)}));
  assert.ok(capped.width*capped.height<=20_000_000,JSON.stringify(capped));
  assert.ok(capped.width<=8192&&capped.height<=8192);
  assert.ok(capped.ratio<3,'DPR3/400% must invoke the pixel budget');
  await dense.close();
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: DPR2 initial '+initial.width+'×'+initial.height+' and 200% '+zoomed.width+'×'+zoomed.height+'; DPR3/400% capped at '+capped.width+'×'+capped.height+'; rapid zoom settled correctly; figure/crop/text annotations created, edited, persisted, reopened in viewer, deleted; source blocks/chats unchanged.');
}catch(error){await shot('figure-resolution-annotation-failure.png');throw error;}finally{await browser.close();}
