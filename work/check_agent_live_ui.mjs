import {chromium} from '../outputs/paper-reader/node_modules/@playwright/test/index.mjs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const browser=await chromium.launch({headless:true,executablePath:'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'});
const page=await browser.newPage({viewport:{width:1500,height:1000}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
try {
  await page.route('**/reading',route=>route.request().method()==='PUT'?route.fulfill({status:200,contentType:'application/json',body:'{"ok":true}'}):route.continue());
  await page.goto('http://127.0.0.1:8765/',{waitUntil:'networkidle'});
  await page.locator('.paper-card').filter({hasText:'MonkeyOCR'}).click();
  await page.getByRole('button',{name:'研究任务'}).click();
  const dialog=page.getByRole('dialog',{name:'研究任务'});
  await dialog.waitFor();
  await dialog.getByRole('heading',{name:/请核对这篇论文图 1/}).waitFor({timeout:15000});
  await page.screenshot({path:resolve(root,'work/agent-live-ui.png'),fullPage:true});
  if(errors.length)throw new Error(errors.join('\n'));
  console.log('Live UI: research task visible and browser errors none.');
} finally { await browser.close(); }
