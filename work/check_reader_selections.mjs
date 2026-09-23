import {createServer} from 'node:http';
import {chromium} from '../outputs/paper-reader/node_modules/@playwright/test/index.mjs';
import {existsSync} from 'node:fs';

const seen=[];
const model=createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  const value=JSON.parse(raw).messages.at(-1).content;
  const info=JSON.parse(Array.isArray(value)?value[0].text:value);
  seen.push(info);
  const citation=info.task==='visual'||!info.evidence?.length?[]:[{block_id:info.evidence[0].block_id,quote:info.evidence[0].text.slice(0,20)}];
  const body=JSON.stringify({choices:[{message:{content:JSON.stringify({answer:'测试回答：当前选区已解释。',citations:citation,supplement:''})},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:10}});
  res.writeHead(200,{'Content-Type':'application/json'});res.end(body);
});
await new Promise(resolve=>model.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:8766';
const settings={provider:'api',base_url:`http://127.0.0.1:${model.address().port}/v1`,api_key:'local-test',model:'fake',vision_model:'fake',call_limit:500};
const saved=await fetch(base+'/api/settings',{method:'PUT',headers:{'X-PaperLoop':'1','Content-Type':'application/json'},body:JSON.stringify(settings)});
if(!saved.ok)throw new Error(await saved.text());
const edge='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const browser=await chromium.launch({headless:true,executablePath:existsSync(edge)?edge:undefined});
const page=await browser.newPage({viewport:{width:1440,height:900}});
try{
  await page.goto(base,{waitUntil:'networkidle'});
  await page.locator('.paper-card').filter({hasText:'MonkeyOCR'}).first().click();
  await page.locator('.paper-page-wrap[data-pdf-page="1"] .textLayer span').first().waitFor();
  async function selectTitle(start,end){
    const ok=await page.evaluate(({start,end})=>{
      const span=[...document.querySelectorAll('.paper-page-wrap[data-pdf-page="1"] .textLayer span')].find(el=>el.textContent?.includes('MonkeyOCR'));
      if(!span?.firstChild)return false;
      const range=document.createRange();range.setStart(span.firstChild,start);range.setEnd(span.firstChild,end);
      window.getSelection()?.removeAllRanges();window.getSelection()?.addRange(range);
      span.closest('.page-sheet')?.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
      return true;
    },{start,end});
    if(!ok)throw new Error('Title text layer not found');
    await page.getByRole('toolbar',{name:'选中文字操作'}).waitFor();
    const response=page.waitForResponse(r=>r.url().endsWith('/ask')&&r.request().method()==='POST');
    await page.getByRole('toolbar',{name:'选中文字操作'}).getByRole('button',{name:'解释'}).click();
    const answer=await response;
    if(!answer.ok())throw new Error('Text QA failed: '+await answer.text());
    await page.getByText('测试回答：当前选区已解释。').first().waitFor({timeout:15000});
  }
  await selectTitle(0,5);
  await selectTitle(5,9);
  if(seen.length!==2||seen[0].context===seen[1].context)throw new Error('Two word selections mixed conversation keys');
  if(seen[0].selected_text===seen[1].selected_text)throw new Error('Second word lost its selected text');
  const scan=page.getByTitle('框选图表或公式');
  await scan.click();
  const sheet=page.locator('.paper-page-wrap[data-pdf-page="1"] .page-sheet');
  const b=await sheet.boundingBox();
  if(!b)throw new Error('PDF page not visible');
  await page.mouse.move(b.x+b.width*.27,b.y+b.height*.36);
  await page.mouse.down();
  await page.mouse.move(b.x+b.width*.48,b.y+b.height*.48,{steps:8});
  await page.mouse.up();
  await page.locator('.context-chip').waitFor();
  const question='框选区域与整图有何关系？';
  await page.getByRole('textbox',{name:'论文问题'}).fill(question);
  const response=page.waitForResponse(r=>r.url().endsWith('/ask')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  const answer=await response;
  if(!answer.ok())throw new Error('Crop QA failed: '+await answer.text());
  await page.getByText(question).first().waitFor({timeout:15000});
  await page.waitForFunction(()=>[...document.querySelectorAll('.chat-pair')].some(el=>el.textContent?.includes('框选区域与整图有何关系')),{timeout:15000});
  if(seen.length!==3||!seen[2].context.startsWith('region:1:crop-'))throw new Error('Manual region anchor mismatch');
  if(seen[2].previous_turns?.some(turn=>turn.question!==question))throw new Error('Manual crop inherited another selection history');
  await page.screenshot({path:'../../work/reader-region-chat.png'});
  await page.reload({waitUntil:'networkidle'});
  await page.locator('.paper-card').filter({hasText:'MonkeyOCR'}).first().click();
  await page.getByRole('button',{name:'提问',exact:true}).click();
  await page.getByRole('button',{name:/全部历史/}).click();
  const chat=page.locator('.chat-pair').filter({hasText:question}).first();
  await chat.getByRole('button',{name:/图像依据/}).click();
  await page.locator('.context-chip').waitFor();
  if(!await page.locator('.conversation-context').getByText(/框选图像/).first().isVisible())throw new Error('Saved crop did not reopen');
  console.log('PASS: two word selections isolated, manual crop QA isolated, saved crop restored');
}finally{await browser.close();model.close();}
