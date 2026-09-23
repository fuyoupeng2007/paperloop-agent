import {createServer} from 'node:http';
import {chromium} from '../outputs/paper-reader/node_modules/@playwright/test/index.mjs';
import {existsSync} from 'node:fs';

const modelInputs=[];
const model=createServer(async(req,res)=>{
  let raw='';
  for await (const chunk of req)raw+=chunk;
  const request=JSON.parse(raw);
  const value=request.messages.at(-1).content;
  const info=JSON.parse(Array.isArray(value)?value[0].text:value);
  modelInputs.push(info);
  const citation=info.task==='visual'||!info.evidence?.length?[]:[{block_id:info.evidence[0].block_id,quote:info.evidence[0].text.slice(0,20)}];
  const body=JSON.stringify({choices:[{message:{content:JSON.stringify({answer:'测试回答：已围绕当前选区解释。',citations:citation,supplement:''})},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:10}});
  res.writeHead(200,{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)});
  res.end(body);
});
await new Promise(resolve=>model.listen(0,'127.0.0.1',resolve));
const modelPort=model.address().port;
const base='http://127.0.0.1:8766';
const config={provider:'api',base_url:`http://127.0.0.1:${modelPort}/v1`,api_key:'local-test',model:'fake',vision_model:'fake',call_limit:500};
const saved=await fetch(base+'/api/settings',{method:'PUT',headers:{'X-PaperLoop':'1','Content-Type':'application/json'},body:JSON.stringify(config)});
if(!saved.ok)throw new Error('Could not set isolated fake model: '+await saved.text());
const edge='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const browser=await chromium.launch({headless:true,executablePath:existsSync(edge)?edge:undefined});
const page=await browser.newPage({viewport:{width:1440,height:900}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
try{
  await page.goto(base,{waitUntil:'networkidle'});
  await page.locator('.paper-card').first().click();
  const badge=page.locator('.pdf-object-badge').filter({hasText:'图 1'}).first();
  await badge.waitFor();
  await badge.click();
  const composer=page.getByRole('textbox',{name:'论文问题'});
  await composer.fill('第一问：图 1 讲什么？');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.getByText('测试回答：已围绕当前选区解释。').first().waitFor({timeout:20000});
  await composer.fill('第二问：这张图的证据是什么？');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-pair').length>=2,{timeout:20000});
  if(modelInputs.length!==2)throw new Error('Expected 2 model requests, got '+modelInputs.length);
  if(modelInputs[0].context!==modelInputs[1].context||!modelInputs[0].context.startsWith('object:figure-'))throw new Error('Figure anchor changed');
  if(modelInputs[1].previous_turns?.length!==1)throw new Error('Figure followup lost first answer');
  if(!modelInputs[1].focus_region?.box)throw new Error('Figure followup lost its crop');
  await page.screenshot({path:'../../work/reader-figure-chat.png'});
  await page.reload({waitUntil:'networkidle'});
  await page.locator('.paper-card').first().click();
  await page.locator('.pdf-object-badge').filter({hasText:'图 1'}).first().click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-pair').length>=2,{timeout:15000});
  if(errors.length)throw new Error('Browser errors: '+errors.join(' | '));
  console.log('PASS: live local fake-model figure QA, same-object followup, crop/context persistence after reload');
}finally{
  await browser.close();
  model.close();
}
