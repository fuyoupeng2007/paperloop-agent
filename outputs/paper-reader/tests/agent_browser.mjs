// Current-source UI smoke with mocked Agent APIs only. No live PaperLoop data.
import {chromium,expect} from '@playwright/test';
import {createServer,transformWithEsbuild} from 'vite';
import react from '@vitejs/plugin-react';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {existsSync,mkdirSync} from 'node:fs';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const images=process.env.PAPERLOOP_TEST_WORK||resolve(root,'../../work/agent-ui-smoke');mkdirSync(images,{recursive:true});
const harness=`import React,{useState} from 'react';import {createRoot} from 'react-dom/client';import {AgentWorkspace} from '/src/AgentWorkspace.tsx';import '/src/style.css';
function Harness(){const [open,setOpen]=useState(true),[ref,setRef]=useState(''),[visual,setVisual]=useState('');return <div className="reading"><main><button onClick={()=>setOpen(true)}>Open agent</button><output aria-label="Citation callback">{ref}</output><output aria-label="Visual callback">{visual}</output>{open&&<AgentWorkspace documentId="doc-one" paperTitle="Research Paper — Agent UI Fixture" onClose={()=>setOpen(false)} onCite={value=>setRef(JSON.stringify(value))} onVisual={value=>setVisual(JSON.stringify(value))}/>}</main></div>}createRoot(document.getElementById('root')).render(<Harness/>);`;
const virtual='\0agent-ui-harness.tsx';
const server=await createServer({root,configFile:false,plugins:[react(),{name:'agent-ui-harness',resolveId(id){if(id==='/__agent_harness.tsx')return virtual;},async load(id){if(id===virtual)return (await transformWithEsbuild(harness,'agent-ui-harness.tsx',{loader:'tsx',jsx:'automatic'})).code;},configureServer(vite){vite.middlewares.use(async(req,res,next)=>{if(req.url!=='/__agent_smoke')return next();res.setHeader('Content-Type','text/html');res.end(await vite.transformIndexHtml(req.url,'<!doctype html><html><head><meta charset="UTF-8"></head><body><div id="root"></div><script type="module" src="/__agent_harness.tsx"></script></body></html>'));});}}],server:{host:'127.0.0.1',port:Number(process.env.PAPERLOOP_AGENT_TEST_PORT||8769),strictPort:true,watch:null}});
let browser;
try{
  await server.listen();const edge='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  browser=await chromium.launch({headless:true,executablePath:process.env.PAPERLOOP_BROWSER_EXECUTABLE||(existsSync(edge)?edge:undefined)});
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});const errors=[],runs=[];let starts=0,polls=0,stage='running',tick=1000,webEnabled=true;
  page.on('pageerror',error=>errors.push(error.message));
  const evidence=[{id:'paper-1',type:'paper',title:'Method paragraph',quote:'Our framework separates layout, recognition and relation prediction.',block_id:'block-one',page:2},{id:'visual-1',type:'visual',title:'Architecture overview',quote:'Three main stages are connected in Figure 1.',page:3,box:[0.1,0.2,0.8,0.7],object_id:'figure-1'},{id:'web-1',type:'web',title:'Official project',quote:'Repository linked from the paper.',url:'https://github.com/example/paper'},{id:'unsafe',type:'web',title:'Invalid source',quote:'This URL must not be clickable.',url:'javascript:alert(1)'}];
  const completedStep={index:1,action:'read_blocks',reason:'核对论文对核心方法的原始定义。',status:'completed',started_at:1000,finished_at:1001,args:{block_ids:['block-one']},observation:{summary:'找到三个阶段及其输入输出的直接定义。',evidence_ids:['paper-1']}};
  function update(run,patch){Object.assign(run,patch,{updated_at:++tick});return run;}
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname,base='/api/documents/doc-one/agent-runs';
    if(path==='/api/documents/doc-one/agent-capabilities')return route.fulfill({json:{tools:[{name:'search_paper',description:'Read the uploaded paper'},...(webEnabled?[{name:'web_search',description:'Look up public sources'}]:[])],notice:webEnabled?'':'当前连接支持论文内阅读，暂时无法联网查证。'}});
    if(path===base&&request.method()==='GET')return route.fulfill({json:runs});
    if(path===base&&request.method()==='POST'){
      starts++;if(starts===1)return route.fulfill({status:503,json:{detail:'Temporary startup failure'}});
      const body=request.postDataJSON();if(!body.goal||body.max_steps!==8)errors.push('Incorrect start payload');
      const run={id:'run-'+runs.length,doc_id:'doc-one',goal:body.goal,status:'running',created_at:++tick,updated_at:tick,allow_web:body.allow_web,max_steps:body.max_steps,max_calls:24,steps_used:0,calls_used:0,plan:['查找方法定义与关键实验','查看相关图表并核对结论'],open_questions:[],steps:[{index:0,action:'deciding',reason:'根据研究目标选择证据入口。',status:'running',started_at:tick,observation:{summary:'',evidence_ids:[]}}],evidence:[],claims:[],limitations:[],review:null,error:'',pause_requested:false,cancel_requested:false,stop_reason:''};runs.unshift(run);stage='running';return route.fulfill({json:run});
    }
    const control=path.match(/\/agent-runs\/(run-\d+)\/control$/);
    if(control&&request.method()==='POST'){
      const run=runs.find(item=>item.id===control[1]),body=request.postDataJSON();
      if(body.action==='pause'){stage='pausing';return route.fulfill({json:update(run,{pause_requested:true})});}
      if(body.action==='cancel'){stage='cancelling';return route.fulfill({json:update(run,{cancel_requested:true})});}
      if(body.action==='resume'){if(![12,16].includes(body.max_steps))errors.push('Resume max_steps missing');stage=body.max_steps===12?'budget':'finishing';return route.fulfill({json:update(run,{status:'running',max_steps:body.max_steps,max_calls:body.max_steps*3,stop_reason:'',error:''})});}
    }
    const detail=path.match(/\/agent-runs\/(run-\d+)$/);
    if(detail&&request.method()==='GET'){
      polls++;const run=runs.find(item=>item.id===detail[1]);
      if(stage==='pausing')update(run,{status:'paused',pause_requested:false,stop_reason:'paused',steps_used:1,steps:[completedStep],evidence:[evidence[0]]});
      if(stage==='budget')update(run,{status:'needs_attention',stop_reason:'budget',steps_used:12,open_questions:['尚需核对图表与代码来源。'],error:'已达到本轮研究步骤上限。'});
      if(stage==='finishing')update(run,{status:'completed',steps_used:14,steps:[completedStep,{...completedStep,index:2,action:'inspect_figure',reason:'把原文定义与结构图对应。',observation:{summary:'图示与三个方法阶段一致。',evidence_ids:['visual-1']}},{...completedStep,index:3,action:'finish',reason:'检查主要结论是否都有可回溯证据。',observation:{summary:'完成证据检查；保留跨数据集泛化的不确定性。',evidence_ids:['paper-1','visual-1','web-1']}}],evidence,claims:[{text:'论文把方法组织为三个阶段，原文与结构图给出了相互一致的说明。',evidence_ids:['paper-1','visual-1']}],limitations:['论文未给出所有跨领域数据的独立复现实验。'],open_questions:[],review:{goal_met:true,checks:[{index:0,supported:true,reason:'方法定义与结构图均支持此结论。'}],missing:[]},error:''});
      if(stage==='cancelling')update(run,{status:'cancelled',cancel_requested:false,stop_reason:'cancelled'});
      return route.fulfill({json:run});
    }
    errors.push('Unexpected API '+request.method()+' '+path);return route.abort();
  });
  await page.goto(server.resolvedUrls.local[0]+'__agent_smoke',{waitUntil:'networkidle'});
  const dialog=page.getByRole('dialog',{name:'研究任务'});
  await expect(dialog.locator('.agent-main')).toHaveCSS('display','block');
  const contained=await dialog.locator('.agent-main').evaluate(element=>{const body=element.closest('.agent-workspace-body').getBoundingClientRect(),content=element.getBoundingClientRect();return content.bottom<=body.bottom+1&&content.right<=body.right+1;});
  if(!contained)throw new Error('Agent workspace exceeds reader modal layout');
  await expect(dialog.getByLabel('最多研究步骤')).toHaveValue('8');
  await expect(dialog.getByRole('checkbox',{name:'允许联网查证'})).not.toBeChecked();
  await dialog.getByRole('button',{name:'作者与 GitHub',exact:true}).click();
  await expect(dialog.getByRole('checkbox',{name:'允许联网查证'})).toBeChecked();
  await expect(dialog.getByLabel('研究目标')).toHaveValue(/GitHub/);
  await dialog.getByRole('button',{name:'方法与证据',exact:true}).click();
  await dialog.getByRole('button',{name:'开始研究',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('Temporary startup failure');
  await expect(dialog.getByLabel('研究目标')).toHaveValue(/核心方法/);
  await dialog.getByRole('button',{name:'开始研究',exact:true}).click();
  await expect(dialog.getByRole('region',{name:'研究步骤'})).toContainText('选择下一步');
  await dialog.getByRole('button',{name:'暂停',exact:true}).click();
  await expect(dialog.getByRole('status')).toContainText('正在进行的查看或查询返回后');
  await expect(dialog.getByRole('button',{name:'继续研究',exact:true})).toBeVisible();
  const pausedPolls=polls;await page.waitForTimeout(2200);if(polls!==pausedPolls)throw new Error('Paused task continued polling');
  await dialog.getByLabel('继续后的总步骤').selectOption('12');await dialog.getByRole('button',{name:'继续研究',exact:true}).click();
  await expect(dialog.getByText('已达到本次步骤上限',{exact:true})).toBeVisible();
  await dialog.getByLabel('继续后的总步骤').selectOption('16');await dialog.getByRole('button',{name:'继续研究',exact:true}).click();
  await expect(dialog.getByText('目标检查通过',{exact:true})).toBeVisible();
  await expect(dialog.locator('.agent-conclusions')).toContainText('论文把方法组织为三个阶段');
  await dialog.locator('.agent-conclusions .agent-evidence-chips button').first().click();
  await expect.poll(()=>dialog.locator('.agent-main').evaluate(element=>element.scrollTop)).toBeGreaterThan(0);
  await dialog.getByRole('button',{name:'回到原文段落'}).click();await expect(page.getByLabel('Citation callback')).toContainText('"block_id":"block-one"');
  await dialog.locator('#agent-evidence-visual-1 summary').click();await dialog.getByRole('button',{name:'查看原图区域'}).click();await expect(page.getByLabel('Visual callback')).toContainText('"box":[0.1,0.2,0.8,0.7]');
  await dialog.locator('#agent-evidence-web-1 summary').click();await expect(dialog.getByRole('link',{name:'打开来源 · github.com'})).toHaveAttribute('href','https://github.com/example/paper');
  await expect(dialog.locator('a[href^="javascript:"]')).toHaveCount(0);
  await dialog.locator('.agent-export summary').click();await expect(dialog.getByRole('link',{name:'Markdown 文档'})).toHaveAttribute('href',/format=md$/);
  await dialog.locator('.agent-conclusions').scrollIntoViewIfNeeded();await page.screenshot({path:resolve(images,'agent-evidence-and-conclusion.png')});
  const completedPolls=polls;await page.waitForTimeout(2000);if(polls!==completedPolls)throw new Error('Completed task continued polling');
  await dialog.getByRole('button',{name:'关闭研究任务'}).click();await page.getByRole('button',{name:'Open agent'}).click();await expect(dialog.locator('.agent-conclusions')).toContainText('三个阶段');
  await dialog.getByRole('button',{name:'新建任务',exact:true}).click();await dialog.getByRole('button',{name:'开始研究',exact:true}).click();await dialog.getByRole('button',{name:'停止任务',exact:true}).click();
  await expect(dialog.getByRole('status')).toContainText('停止请求已收到');await expect(dialog.locator('.agent-status.cancelled')).toContainText('已停止');
  await expect(dialog.locator('.agent-history-item')).toHaveCount(2);
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
  webEnabled=false;await page.getByRole('button',{name:'Open agent'}).click();await dialog.getByRole('button',{name:'新建任务',exact:true}).click();
  await expect(dialog.getByRole('checkbox',{name:'允许联网查证'})).toBeDisabled();
  await expect(dialog.getByText('当前连接支持论文内阅读，暂时无法联网查证。',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'作者与 GitHub',exact:true}).click();await expect(dialog.getByRole('checkbox',{name:'允许联网查证'})).not.toBeChecked();
  await dialog.getByLabel('研究目标').fill('读');await expect(dialog.getByRole('button',{name:'开始研究',exact:true})).toBeDisabled();
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
  if(errors.length)throw new Error(errors.join(' | '));
  console.log('Agent UI passed: real .reading container layout/scroll, goal/web options and capability gating, start failure/retry, plan and observations, step-boundary pause, active-only polling, resume/budget extension, conclusions/evidence callbacks, safe source links, export, history restoration, cancel.');
  console.log('Screenshot:',resolve(images,'agent-evidence-and-conclusion.png'));
}finally{await browser?.close();await server.close();}
