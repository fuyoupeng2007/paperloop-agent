import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {chromium} from '../outputs/paper-reader/node_modules/@playwright/test/index.mjs';
const browser=await chromium.launch({headless:true,executablePath:'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'});
const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto('http://127.0.0.1:8766',{waitUntil:'networkidle'});
  await page.locator('.paper-card').filter({hasText:'MonkeyOCR'}).first().click();
  await page.getByLabel('当前页').selectOption('1');
  const note=page.getByRole('button',{name:'编辑批注：浏览器验证：整图批注已编辑。',exact:true});
  const position=await note.boundingBox();
  const original=await page.locator('.pdf-object-badge').filter({hasText:'图 1'}).first().boundingBox();
  assert.ok(position.x>original.x+original.width,'Note badge must leave Figure 1 handle unobscured');
  await note.click();
  await page.getByRole('dialog',{name:'编辑批注',exact:true}).waitFor();
  await page.getByRole('button',{name:'关闭批注',exact:true}).click();
  await page.locator('.pdf-object-badge').filter({hasText:'图 1'}).first().dblclick();
  await page.waitForFunction(()=>document.querySelector('.figure-image-layer canvas')?.dataset.renderZoom==='1');
  await page.locator('.figure-viewer').getByRole('button',{name:'编辑批注：浏览器验证：整图批注已编辑。',exact:true}).click();
  assert.equal(await page.locator('.figure-viewer').count(),0);
  await page.getByRole('dialog',{name:'编辑批注',exact:true}).waitFor();
  await page.getByRole('button',{name:'关闭批注',exact:true}).click();
  await page.screenshot({path:fileURLToPath(new URL('annotations-final-badges.png',import.meta.url))});
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: relocated annotation badges remain clickable on PDF and figure viewer, leave original Figure 1 handle exposed, editor opens after viewer closes.');
}finally{await browser.close();}
