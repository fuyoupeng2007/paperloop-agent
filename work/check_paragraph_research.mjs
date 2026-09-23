import {chromium,expect} from '../outputs/paper-reader/node_modules/@playwright/test/index.mjs';
import {readFileSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const work=dirname(fileURLToPath(import.meta.url));
const makeBlock=(id,order,text,translation)=>({id,page:1,order,text,translation,kind:'text',section:'Introduction',status:'done',error:'',bbox:[.08,.2+order*.15,.85,.28+order*.15]});
const blocks=[makeBlock('a',0,'Paragraph A introduces the main research question.','第一段介绍研究问题。'),makeBlock('b',1,'Paragraph B explains the method and evidence.','第二段解释方法与证据。'),{...makeBlock('c',0,'The next page discusses results.','第二页讨论结果。'),page:2}];
const chat=(id,context,answer)=>({id,question:'已有问题 '+id,answer,supplement:'',citations:[{block_id:context==='selection:b'?'b':'a',page:1,quote:'source'}],coverage:'参考一个段落。',visual_page:null,context_key:context,page:1});
const report={publication:{status:'published',venue:'测试期刊',year:2026,detail:'仅供界面验证的发表信息。',source_ids:['s1']},authors:[{name:'测试作者',affiliation:'测试机构',role:'作者',detail:'用于界面验证。',source_ids:['s1']}],significance:[{title:'分析与论文主张分开',detail:'仅供界面验证的意义描述。',kind:'analysis',source_ids:['s1']}],sources:[{id:'s1',title:'可核对的测试来源',url:'https://example.org/paper'},{id:'s2',title:'不可用测试链接',url:'javascript:alert(1)'}],summary:'上次已保存的背景报告。',limitations:['作者角色仍需进一步核实。']};
const doc={id:'ui-fixture',title:'Paragraph Interaction Fixture',title_translation:'逐段交互验证',filename:'fixture.pdf',page_count:2,reading_page:1,status:'completed',message:'全文处理完成',worker_busy:false,created_at:Date.now()/1000,blocks,pages:[{number:1,status:'done',source:'text',warning:''},{number:2,status:'done',source:'text',warning:''}],notes:[],chats:[chat('legacy',undefined,'旧记录属于整篇论文。'),chat('a-old','selection:a','A 范围的历史。'),chat('b-old','selection:b','B 范围的历史。')],glossary:[],events:[],usage:{calls:0,input_tokens:0,output_tokens:0,estimated_cost:0},research:{status:'completed',updated_at:Date.now()/1000,report,queries:['测试论文标题']}};
const requests=[],errors=[];
let releaseAsk,failNext=false;
const browser=await chromium.launch({headless:true,executablePath:process.env.PAPERLOOP_BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
const page=await browser.newPage({viewport:{width:1440,height:960}});
page.on('pageerror',error=>errors.push(error.message));
await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
  const respond=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(path==='/api/settings')return respond({provider:'codex',configured:true,engine:{available:true,logged_in:true,label:'ChatGPT',message:'测试连接'},base_url:'',model:'',vision_model:'',has_key:false,call_limit:500,input_price:0,output_price:0});
  if(path==='/api/documents'&&method==='GET')return respond([doc]);
  if(path==='/api/documents/ui-fixture'&&method==='GET')return respond(doc);
  if(path.endsWith('/pdf'))return route.fulfill({contentType:'application/pdf',body:readFileSync(resolve(work,'browser-two-page.pdf'))});
  if(path.endsWith('/reading')&&method==='PUT'){requests.push({type:'reading',...request.postDataJSON()});return respond({ok:true});}
  if(path.endsWith('/ask')&&method==='POST'){
    const input=request.postDataJSON();requests.push({type:'ask',...input});
    if(failNext){failNext=false;return respond({detail:'模拟连接中断，请重试。'},502);}
    if(!releaseAsk)await new Promise(done=>{releaseAsk=done;});
    const context=input.scope==='selection'?'selection:'+input.block_id:input.scope==='section'?'section:Introduction':input.scope==='page'?'page:'+input.page:'paper';
    const result={...chat('new-'+doc.chats.length,context,'本次回答已绑定正确范围。'),question:input.question,scope:input.scope,block_id:input.block_id,page:input.page};
    result.citations=[{block_id:input.scope==='section'||input.scope==='page'?'a':'c',page:input.scope==='section'||input.scope==='page'?1:2,quote:'source for navigation'}];
    doc.chats.push(result);return respond(result);
  }
  if(path.endsWith('/research')&&method==='POST'){requests.push({type:'research'});doc.research={...doc.research,status:'running',error:''};return respond({status:'running'});}
  errors.push('Unexpected API request: '+method+' '+path);
  return respond({detail:'Test refuses unmocked API access'},409);
});

try{
  await page.goto(process.env.PAPERLOOP_BASE_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
  await page.locator('.paper-card').click();
  const a=page.locator('#block-a'),b=page.locator('#block-b');
  await a.getByRole('button',{name:'问这段',exact:true}).click();
  await expect(page.getByLabel('论文问题')).toBeFocused();
  await expect(page.getByLabel('提问范围')).toHaveValue('selection');
  await expect(page.getByLabel('当前问答上下文')).toContainText('第一段介绍研究问题');
  await expect(page.getByText('A 范围的历史。',{exact:true})).toBeVisible();
  await expect(page.getByText('B 范围的历史。',{exact:true})).toHaveCount(0);

  await b.getByRole('button',{name:'解释',exact:true}).click();
  await expect.poll(()=>requests.filter(r=>r.type==='ask').length).toBe(1);
  expect(requests.find(r=>r.type==='ask')).toMatchObject({mode:'explain',scope:'selection',block_id:'b',page:1});
  await expect(page.getByText('正在阅读原文并组织回答…')).toBeVisible();
  await expect(page.getByText('A 范围的历史。',{exact:true})).toHaveCount(0);
  releaseAsk();
  await expect(page.getByText('本次回答已绑定正确范围。',{exact:true})).toBeVisible();
  await page.locator('.chat-pair').last().locator('.citations button').first().click();
  await expect(page.getByLabel('当前页')).toHaveValue('2');
  await expect(page.getByLabel('提问范围')).toHaveValue('selection');
  await expect(page.getByLabel('当前问答上下文')).toContainText('PDF 第 1 页');
  await page.getByLabel('论文问题').fill('再解释一下这里的前提。');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect.poll(()=>requests.filter(r=>r.type==='ask').length).toBe(2);
  expect(requests.filter(r=>r.type==='ask')[1]).toMatchObject({scope:'selection',block_id:'b',page:1});
  await expect(page.locator('.thinking')).toHaveCount(0);
  async function askInCurrentScope(question){const count=requests.filter(r=>r.type==='ask').length;await page.getByLabel('论文问题').fill(question);await page.getByRole('button',{name:'发送',exact:true}).click();await expect.poll(()=>requests.filter(r=>r.type==='ask').length).toBe(count+1);await expect(page.locator('.thinking')).toHaveCount(0);}
  await page.getByLabel('提问范围').selectOption('section');
  await askInCurrentScope('当前章节的逻辑是什么？');
  await page.locator('.chat-pair').last().locator('.citations button').first().click();
  await expect(page.getByLabel('当前页')).toHaveValue('1');
  await expect(page.getByLabel('提问范围')).toHaveValue('section');
  await askInCurrentScope('继续解释这个章节。');
  expect(requests.filter(r=>r.type==='ask').at(-1)).toMatchObject({scope:'section',block_id:'b',page:1});
  await page.getByTitle('下一页',{exact:true}).click();
  await expect(page.getByLabel('提问范围')).toHaveValue('page');
  await askInCurrentScope('请解释第二页。');
  await page.locator('.chat-pair').last().locator('.citations button').first().click();
  await expect(page.getByLabel('当前页')).toHaveValue('1');
  await expect(page.getByLabel('当前问答上下文')).toContainText('PDF 第 2 页');
  await askInCurrentScope('继续解释这一页。');
  expect(requests.filter(r=>r.type==='ask').at(-1)).toMatchObject({scope:'page',block_id:null,page:2});
  await page.getByRole('button',{name:/全部历史/}).click();
  await expect(page.getByText('A 范围的历史。',{exact:true})).toBeVisible();
  await expect(page.getByText('旧记录属于整篇论文。',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'切到整篇',exact:true}).click();
  await expect(page.getByLabel('提问范围')).toHaveValue('paper');
  await expect(page.getByText('旧记录属于整篇论文。',{exact:true})).toBeVisible();
  await expect(page.getByText('A 范围的历史。',{exact:true})).toHaveCount(0);

  failNext=true;
  await a.getByRole('button',{name:'解释',exact:true}).click();
  await expect(page.locator('.chat-error')).toContainText('模拟连接中断');
  await page.getByRole('button',{name:'重试这条问题'}).click();
  await expect(page.locator('.chat-error')).toHaveCount(0);
  await expect.poll(()=>requests.filter(r=>r.type==='ask').length).toBe(8);
  expect(requests.filter(r=>r.type==='ask')[7]).toMatchObject({scope:'selection',block_id:'a',mode:'explain'});
  await page.screenshot({path:resolve(work,'paragraph-context-check.png'),fullPage:true});

  await page.getByRole('button',{name:'论文背景',exact:true}).click();
  await expect(page.getByText('上次已保存的背景报告。',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'更新论文背景'}).click();
  await expect(page.getByText(/正在查找公开资料并整理来源/)).toBeVisible();
  await expect(page.getByText('上次已保存的背景报告。',{exact:true})).toBeVisible();
  expect(await page.locator('.research-scroll a[href^="javascript:"]').count()).toBe(0);
  for(const link of await page.locator('.research-scroll a').all()){
    expect(await link.getAttribute('href')).toMatch(/^https?:/);
    expect(await link.getAttribute('target')).toBe('_blank');
    expect(await link.getAttribute('rel')).toContain('noopener');
  }
  doc.research={...doc.research,status:'completed',updated_at:Date.now()/1000,report:{...report,summary:'新背景报告已经自动更新。'}};
  await expect(page.getByText('新背景报告已经自动更新。',{exact:true})).toBeVisible({timeout:7000});
  await page.screenshot({path:resolve(work,'research-panel-check.png'),fullPage:true});
  expect(errors).toEqual([]);
  console.log(JSON.stringify({ok:true,askRequests:requests.filter(r=>r.type==='ask').length,researchRequests:requests.filter(r=>r.type==='research').length,allAPIRoutesMocked:true,screenshots:['paragraph-context-check.png','research-panel-check.png']},null,2));
}finally{await browser.close();}
