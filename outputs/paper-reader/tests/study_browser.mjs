// Isolated component smoke: Vite serves current source; every API request is mocked.
// This test never contacts PaperLoop's running backend or writes its data.
import {chromium,expect} from '@playwright/test';
import {createServer,transformWithEsbuild} from 'vite';
import react from '@vitejs/plugin-react';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {existsSync,mkdirSync} from 'node:fs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const images=process.env.PAPERLOOP_TEST_WORK||resolve(root,'../../work/study-ui-smoke');
mkdirSync(images,{recursive:true});
const harness=`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ComparisonWorkspace} from '/src/ComparisonWorkspace.tsx';
import {ResearchPanel,ReadingLookupPanel} from '/src/ResearchPanel.tsx';
import '/src/style.css';
const papers=Array.from({length:6},(_,i)=>({id:'paper-'+(i+1),title:'Research Paper '+(i+1),page_count:12}));
const sources=[{id:'s1',title:'University profile',url:'https://example.org/profile'}];
const report={summary:'A verified research background.',publication:{status:'preprint',venue:'arXiv',year:2026,detail:'Preprint record',source_ids:['s1']},authors:[{name:'Alice Smith',affiliation:'Example University',role:'First author',detail:'Identity matched with this paper.',source_ids:['s1']}],significance:[],sources,limitations:[]};
const lookups={author1:{status:'completed',kind:'author',query:'Alice Smith',updated_at:1000,result:{answer:'Alice works at Example University.',relationships:[{subject:'Alice Smith',relation:'affiliated with',object:'Example University',source_url:'https://example.org/profile'}],related:[],sources:[{title:'University profile',url:'https://example.org/profile',claim:'Lists affiliation.'},{title:'Invalid source',url:'javascript:alert(1)',claim:'Must not be clickable.'}],limitations:['Supervisor not verified.']}}};
const github=[{url:'https://github.com/alice-research',title:'Alice research account',kind:'personal',verification:'verified',detail:'University profile links this account.',source_urls:['https://github.com/alice-research','https://example.org/profile']},{url:'https://github.com/example-lab',title:'Example laboratory',kind:'organization',verification:'verified',detail:'University page identifies this organization.',source_urls:['https://github.com/example-lab','https://example.org/profile']},{url:'https://github.com/example-lab/paper-model',title:'Paper model repository',kind:'project',verification:'verified',detail:'Official project page points to this repository.',source_urls:['https://github.com/example-lab/paper-model','https://example.org/profile']},{url:'https://github.com/alice-candidate',title:'Same-name candidate',kind:'personal',verification:'unverified',detail:'Name matches, identity is not established.',source_urls:['https://github.com/alice-candidate']}];
lookups.author1.result.github=github;
lookups.author1.result.sources.push(...github.map(item=>({title:item.title,url:item.url,claim:item.detail})));
lookups.institution1={status:'completed',kind:'institution',query:'Example University',updated_at:1000,result:{answer:'An institution result saved before GitHub support.',relationships:[],related:[],sources:[],limitations:[]}};
function Harness(){const [open,setOpen]=useState(true),[citation,setCitation]=useState(''),[lookup,setLookup]=useState('');return <main style={{padding:20}}><button onClick={()=>setOpen(true)}>Open comparison</button><output aria-label="Citation callback">{citation}</output><output aria-label="Lookup callback">{lookup}</output><section aria-label="Author research" style={{maxWidth:420}}><ResearchPanel research={{status:'completed',report}} lookups={lookups} onStart={async()=>{}} onLookup={async(kind,query)=>setLookup(kind+'|'+query)} paperTitle="Research Paper 1"/></section><section aria-label="Research without report" style={{maxWidth:420}}><ResearchPanel onStart={async()=>{}} onLookup={async(kind,query)=>setLookup(kind+'|'+query)} paperTitle="New Paper"/></section><section aria-label="Term lookup" style={{maxWidth:420}}><ReadingLookupPanel lookups={{}} initialQuery="" onLookup={async(kind,query)=>setLookup(kind+'|'+query)}/></section>{open&&<ComparisonWorkspace papers={papers} onClose={()=>setOpen(false)} onOpenPaper={(id,page,block,quote)=>setCitation(JSON.stringify({id,page,block,quote}))}/>}</main>}
createRoot(document.getElementById('root')).render(<Harness/>);`;
const virtual='\0study-harness.tsx';
const server=await createServer({root,configFile:false,plugins:[react(),{name:'study-smoke-harness',resolveId(id){if(id==='/__study_harness.tsx')return virtual;},async load(id){if(id===virtual)return (await transformWithEsbuild(harness,'study-harness.tsx',{loader:'tsx',jsx:'automatic'})).code;},configureServer(vite){vite.middlewares.use(async(req,res,next)=>{if(req.url==='/__study_smoke'){res.setHeader('Content-Type','text/html');res.end(await vite.transformIndexHtml(req.url,'<!doctype html><html><head><meta charset="UTF-8"></head><body><div id="root"></div><script type="module" src="/__study_harness.tsx"></script></body></html>'));}else next();});}}],server:{host:'127.0.0.1',port:Number(process.env.PAPERLOOP_STUDY_TEST_PORT||8768),strictPort:true,watch:null}});
let browser;
try{
  await server.listen();
  const edge='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  browser=await chromium.launch({headless:true,executablePath:process.env.PAPERLOOP_BROWSER_EXECUTABLE||(existsSync(edge)?edge:undefined)});
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  const errors=[],saved=[];let askAttempts=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(path==='/api/comparisons'&&request.method()==='GET')return route.fulfill({json:saved});
    if(path==='/api/comparisons'&&request.method()==='POST'){
      const body=request.postDataJSON();
      if(body.document_ids.join(',')!=='paper-1,paper-2')throw new Error('Unexpected comparison selection');
      const result={id:'comparison-1',created_at:1770000000,papers:body.document_ids.map((id,i)=>({id,title:'Research Paper '+(i+1)})),rows:body.dimensions.map(dimension=>({dimension,cells:[...body.document_ids].reverse().map(id=>({document_id:id,claim:id==='paper-1'?'First paper evidence':'Second paper evidence',block_id:'block-'+id,page:2,quote:'Exact quote from '+id,evidence_scope:'PDF 原文'}))})),summary:'Evidence supports a comparison of different approaches.',limitations:['Datasets differ; do not rank raw scores.'],chats:[]};
      saved.unshift(result);return route.fulfill({json:result});
    }
    if(path==='/api/comparisons/comparison-1/ask'&&request.method()==='POST'){
      askAttempts++;
      if(askAttempts===1)return route.fulfill({status:503,json:{detail:'Temporary test failure'}});
      const body=request.postDataJSON();
      const chat={id:'chat-1',question:body.question,answer:'The datasets differ, so the scores are not directly comparable.',supplement:'Check the evaluation setup.',coverage:'Both selected papers have verified excerpts.',citations:[{document_id:'paper-2',block_id:'block-paper-2',page:3,quote:'Exact followup evidence.'}],created_at:1770000100};
      saved[0].chats.push(chat);return route.fulfill({json:chat});
    }
    errors.push('Unexpected backend request: '+request.method()+' '+path);return route.abort();
  });
  await page.goto(server.resolvedUrls.local[0]+'__study_smoke',{waitUntil:'networkidle'});
  const dialog=page.getByRole('dialog',{name:'跨论文比较'}),choices=dialog.locator('.comparison-papers input');
  await expect(dialog.getByRole('button',{name:'生成比较表'})).toBeDisabled();
  for(let i=0;i<5;i++)await choices.nth(i).check();
  await expect(choices.nth(5)).toBeDisabled();
  for(let i=2;i<5;i++)await choices.nth(i).uncheck();
  await dialog.getByRole('button',{name:'生成比较表'}).click();
  await expect(dialog.locator('tbody tr').first().locator('td').first()).toContainText('First paper evidence');
  await dialog.getByRole('button',{name:'PDF p.2 · 查看原文'}).first().click();
  await expect(page.getByLabel('Citation callback')).toContainText('"block":"block-paper-1"');
  await dialog.getByLabel('你的问题').fill('Are these scores directly comparable?');
  await dialog.getByRole('button',{name:'发送追问'}).click();
  await expect(dialog.getByRole('alert')).toContainText('Temporary test failure');
  await expect(dialog.getByLabel('你的问题')).toHaveValue('Are these scores directly comparable?');
  await dialog.getByRole('button',{name:'发送追问'}).click();
  await expect(dialog.locator('.comparison-chat')).toContainText('The datasets differ');
  await expect(dialog.getByLabel('你的问题')).toHaveValue('');
  await dialog.locator('.comparison-chat summary').click();
  await dialog.getByRole('button',{name:'查看对应 PDF 原文'}).click();
  await expect(page.getByLabel('Citation callback')).toContainText('"page":3');
  await page.screenshot({path:resolve(images,'comparison-followup.png'),fullPage:true});
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'Open comparison'}).click();
  await expect(dialog.locator('.comparison-chat')).toContainText('The datasets differ');
  await expect(choices.nth(0)).toBeChecked();
  await expect(choices.nth(1)).toBeChecked();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  const author=page.getByRole('region',{name:'Author research'});
  await expect(author.locator('.lookup-relations')).toContainText('Alice Smith → affiliated with → Example University');
  await expect(author.getByRole('link',{name:'查看来源 ↗'})).toHaveAttribute('href','https://example.org/profile');
  await expect(author.locator('a[href^="javascript:"],a[href="#"]')).toHaveCount(0);
  await expect(author.locator('.lookup-github-entry')).toHaveCount(4);
  await expect(author.locator('.github-status.verified')).toHaveCount(3);
  await expect(author.locator('.github-status.unverified')).toHaveCount(1);
  await expect(author.locator('.github-target').filter({hasText:'Alice research account'})).toHaveAttribute('href','https://github.com/alice-research');
  await expect(author.locator('.github-target').filter({hasText:'Paper model repository'})).toHaveAttribute('href','https://github.com/example-lab/paper-model');
  await expect(author.getByText('这条旧记录尚未单独核对 GitHub。点击“更新查证”可补查个人主页与项目仓库。')).toBeVisible();
  await author.getByRole('button',{name:'查身份、关系与 GitHub'}).click();
  await expect(page.getByLabel('Lookup callback')).toHaveText('author|Alice Smith');
  await author.getByRole('button',{name:'查机构背景：Example University'}).click();
  await expect(page.getByLabel('Lookup callback')).toHaveText('institution|Example University');
  await page.getByRole('region',{name:'Research without report'}).getByRole('button',{name:'联网查找相关工作'}).click();
  await expect(page.getByLabel('Lookup callback')).toHaveText('related|New Paper');
  const term=page.getByRole('region',{name:'Term lookup'});
  await term.getByRole('textbox',{name:'联网检索主题'}).fill('Document parsing');
  await term.getByRole('button',{name:'联网查',exact:true}).click();
  await expect(page.getByLabel('Lookup callback')).toHaveText('term|Document parsing');
  await author.scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(images,'research-sources.png'),fullPage:true});
  if(errors.length)throw new Error('Browser errors: '+errors.join(' | '));
  console.log('Study UI smoke passed: 2–5 selection, column order, exact evidence callback, followup failure/retry/save/reopen, author sources, verified/candidate GitHub cards and legacy data, institution query, related work, term lookup.');
  console.log('Screenshots:',images);
}finally{await browser?.close();await server.close();}
