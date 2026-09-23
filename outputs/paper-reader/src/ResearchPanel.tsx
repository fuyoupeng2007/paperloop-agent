import {useEffect,useState} from 'react';
import {ArrowUpRight, CircleAlert, Github, Globe2, LoaderCircle, RefreshCw} from 'lucide-react';

type Source={id:string;title:string;url:string};
type Report={
  publication:{status:string;venue:string;year:string|number|null;detail:string;source_ids:string[]};
  authors:{name:string;affiliation:string;role:string;detail:string;source_ids:string[]}[];
  significance:{title:string;detail:string;kind:'paper_claim'|'context'|'analysis';source_ids:string[]}[];
  sources:Source[];summary:string;limitations:string[];
};
export type ResearchState={status:'idle'|'running'|'completed'|'error';error?:string;updated_at?:number;report?:Report|null;queries?:string[]};
type GithubEntry={url:string;title:string;kind:'personal'|'organization'|'project';verification:'verified'|'unverified';detail:string;source_urls:string[]};
export type LookupResult={answer:string;sources:{title:string;url:string;claim:string}[];relationships:{subject:string;relation:string;object:string;source_url:string}[];related:{title:string;year:string;url:string;reason:string}[];limitations:string[];github?:GithubEntry[]};
export type LookupRecord={status:string;kind:string;query:string;result?:LookupResult;error?:string;updated_at?:number;started_at?:number};

function safeURL(value:string){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}}
function sourceDomain(value:string){const url=safeURL(value);return url?new URL(url).hostname:'';}
function normalizeQuery(value:string){return value.trim().replace(/\s+/g,' ').toLocaleLowerCase();}
function newestLookups(lookups:Record<string,LookupRecord>,kind:string){return Object.entries(lookups).filter(([,entry])=>entry.kind===kind).sort(([,a],[,b])=>(b.started_at||b.updated_at||0)-(a.started_at||a.updated_at||0));}
function SourceLinks({ids,sources}:{ids:string[];sources:Source[]}){
  return <div className="research-citations">{[...new Set(ids)].map(id=>{const source=sources.find(s=>s.id===id);const url=source&&safeURL(source.url);return source&&url?<a key={id} href={url} target="_blank" rel="noopener noreferrer" title={sourceDomain(url)}>{source.title}<ArrowUpRight size={12}/></a>:null;})}</div>;
}

function GithubLinks({result}:{result:LookupResult}){
  const kinds={personal:'个人主页',organization:'组织主页',project:'项目仓库'};
  return <div className="lookup-github"><h5><Github size={14}/>GitHub 主页与项目</h5>{result.github?.length?result.github.map((item,i)=>{
    const url=safeURL(item.url),target=url?new URL(url):null,valid=target?.hostname==='github.com',slug=target?.pathname.slice(1)||item.title;
    const references=[...new Set(item.source_urls||[])].map(value=>result.sources?.find(source=>source.url===value)).filter((source):source is LookupResult['sources'][number]=>!!source&&!!safeURL(source.url));
    const verified=item.verification==='verified'&&valid&&!!item.detail.trim()&&references.length>=2&&references.some(source=>safeURL(source.url)===url);
    return <article className="lookup-github-entry research-entry" key={i}>
      <div className="github-badges"><span className="research-badge">{kinds[item.kind]||'GitHub 链接'}</span><span className={'github-status research-badge '+(verified?'verified':'unverified')}>{verified?'归属已核实':'归属待核实'}</span></div>
      {url&&valid?<a className="github-target" href={url} target="_blank" rel="noopener noreferrer"><Github size={14}/><span>{item.title||slug}<small>{slug}</small></span><ArrowUpRight size={12}/></a>:<strong>{item.title||'GitHub 链接'}（链接不可用）</strong>}
      {item.detail&&<p>{item.detail}</p>}{!verified&&<p className="small muted">这是候选链接，尚不能确认与当前作者或机构的归属关系。</p>}
      {!!references.length&&<div className="research-citations" aria-label="GitHub 归属证据">{references.map((source,index)=><a key={source.url} href={safeURL(source.url)!} target="_blank" rel="noopener noreferrer" title={sourceDomain(source.url)}>核对来源 {index+1}：{source.title}<ArrowUpRight size={12}/></a>)}</div>}
    </article>;
  }):<p className="small muted">{result.github===undefined?'这条旧记录尚未单独核对 GitHub。点击“更新查证”可补查个人主页与项目仓库。':'本次未找到可核对的 GitHub 主页或项目仓库。'}</p>}</div>;
}

export function LookupCard({entry,onRetry,disabled=false}:{entry:LookupRecord;onRetry?:()=>void;disabled?:boolean}){
  const result=entry.result;
  return <article className="lookup-card"><strong>{entry.query}</strong>
    {entry.status==='running'&&<p role="status"><LoaderCircle size={12} className="spin"/> 正在联网查证…{result?'下方保留上次结果。':''}</p>}
    {(entry.error||entry.status==='error')&&<p className="error-inline" role="alert">{entry.error||'本次查证未完成，请重试。'}{result?' 下方是上次保存的结果。':''}</p>}
    {entry.updated_at&&<small>查证时间：{new Date(entry.updated_at*1000).toLocaleString('zh-CN')}</small>}
    {result&&<><span className="research-badge">{entry.kind==='institution'?'机构背景 · ':''}外部公开资料</span><p>{result.answer}</p>
      {(entry.kind==='author'||entry.kind==='institution')&&<GithubLinks result={result}/>}
      {!!result.relationships?.length&&<div className="lookup-relations"><b>有来源的关系</b>{result.relationships.map((edge,i)=>{const url=safeURL(edge.source_url);return <div key={i}><span>{edge.subject} → {edge.relation} → {edge.object}</span>{url?<a href={url} target="_blank" rel="noopener noreferrer" title={sourceDomain(url)}> 查看来源 ↗</a>:<small>（来源链接不可用）</small>}</div>;})}</div>}
      {!!result.related?.length&&<div className="lookup-relations"><b>相关工作</b>{result.related.map((paper,i)=>{const url=safeURL(paper.url);return <div key={i}>{url?<a href={url} target="_blank" rel="noopener noreferrer" title={sourceDomain(url)}>{paper.title} ↗</a>:<span>{paper.title}（来源链接不可用）</span>} {paper.year}<p>{paper.reason}</p></div>;})}</div>}
      {!!result.sources?.length&&<details className="lookup-sources" open><summary>公开来源 · {result.sources.length}</summary>{result.sources.map((source,i)=>{const url=safeURL(source.url);return url?<a className="lookup-source" key={i} href={url} target="_blank" rel="noopener noreferrer">{source.title} ↗<small>{sourceDomain(url)}</small><small>{source.claim}</small></a>:<div className="lookup-source" key={i}>{source.title}（链接不可用）<small>{source.claim}</small></div>;})}</details>}
      {!!result.limitations?.length&&<div className="lookup-limitations"><b>待核实与来源限制</b><ul>{result.limitations.map((limitation,i)=><li key={i}>{limitation}</li>)}</ul></div>}
    </>}
    {onRetry&&entry.status!=='running'&&<button className="research-deep" disabled={disabled} onClick={onRetry}>{entry.status==='error'?'重新查证':'更新查证'}</button>}
  </article>;
}

export function ReadingLookupPanel({lookups,initialQuery,onLookup}:{lookups:Record<string,LookupRecord>;initialQuery:string;onLookup:(kind:'term',query:string)=>Promise<void>}){
  const [query,setQuery]=useState(initialQuery),[submitting,setSubmitting]=useState(false),[error,setError]=useState('');
  useEffect(()=>setQuery(initialQuery),[initialQuery]);
  const running=submitting||Object.values(lookups).some(entry=>entry.status==='running'),entries=newestLookups(lookups,'term');
  async function search(value:string){const text=value.trim();if(running||text.length<2||text.length>240)return;setSubmitting(true);setError('');try{await onLookup('term',text);}catch(e){setError((e as Error).message);}finally{setSubmitting(false);}}
  return <div className="research-scroll"><div className="research-intro"><span><Globe2 size={23}/></span><h3>阅读中联网查</h3><p>查论文中的术语、方法或机构；结果附公开来源，并与这篇论文一起保存在本机。</p></div><form className="lookup-search" onSubmit={e=>{e.preventDefault();void search(query);}}><input aria-label="联网检索主题" maxLength={240} value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入术语、方法或机构"/><button className="primary" type="submit" disabled={running||query.trim().length<2||query.trim().length>240}>{running?'查证中…':'联网查'}</button></form>{error&&<p className="error-inline" role="alert">{error}</p>}{running&&<p className="small muted" role="status">正在完成本篇论文的联网查证，完成后可继续查找。</p>}{entries.map(([key,entry])=><LookupCard key={key} entry={entry} disabled={running} onRetry={()=>void search(entry.query)}/>)}{!entries.length&&<p className="small muted">在正文中划词，点击“联网查”，即可把查证结果和来源放在这里。</p>}</div>;
}

export function ResearchPanel({research,onStart,lookups={},onLookup,paperTitle}:{research?:ResearchState;onStart:()=>Promise<void>;lookups?:Record<string,LookupRecord>;onLookup:(kind:'author'|'institution'|'related',query:string)=>Promise<void>;paperTitle:string}){
  const [submitting,setSubmitting]=useState(false),[lookupSubmitting,setLookupSubmitting]=useState(false),[error,setError]=useState('');
  const running=submitting||research?.status==='running',report=research?.report;
  const lookupRunning=lookupSubmitting||Object.values(lookups).some(entry=>entry.status==='running');
  const sources=report?.sources||[];
  async function start(){if(running)return;setSubmitting(true);setError('');try{await onStart();}catch(e){setError((e as Error).message);}finally{setSubmitting(false);}}
  async function lookup(kind:'author'|'institution'|'related',query:string){if(lookupRunning)return;setLookupSubmitting(true);setError('');try{await onLookup(kind,query.trim().slice(0,240));}catch(e){setError((e as Error).message);}finally{setLookupSubmitting(false);}}
  const statusNames:Record<string,string>={published:'已发表',preprint:'预印本',accepted:'已录用',unpublished:'未发表',unknown:'尚待核实',not_found:'未找到确切记录'};
  const kindNames={paper_claim:'论文主张',context:'研究背景',analysis:'综合分析'};
  return <div className="research-scroll">
    <div className="research-intro"><span><Globe2 size={23}/></span><h3>把这篇论文放回研究背景</h3><p>联网整理发表情况、作者与研究意义，来源链接供你逐项核对。</p><button className="primary" disabled={running} onClick={()=>void start()}>{running?<LoaderCircle size={15} className="spin"/>:report?<RefreshCw size={15}/>:<Globe2 size={15}/>} {running?'正在查找背景…':report?'更新论文背景':'查找论文背景'}</button></div>
    {running&&<p className="research-running" role="status">正在查找公开资料并整理来源，完成后会自动显示。{report?'你可以先阅读上次的结果。':''}</p>}
    {(error||research?.status==='error')&&<div className="error-inline" role="alert"><CircleAlert size={15}/><span>{error||research?.error||'这次背景查询未完成，请重试。'}{report?' 已保留上次的结果。':''}</span></div>}
    {report&&<>
      {research?.updated_at&&<p className="research-updated">上次更新：{new Date(research.updated_at*1000).toLocaleString('zh-CN')}</p>}
      {report.summary&&<p className="research-summary">{report.summary}</p>}
      <section className="research-section"><h4>发表情况</h4><div className="research-entry"><span className="research-badge">{statusNames[report.publication.status]||report.publication.status||'尚待核实'}</span><strong>{[report.publication.venue,report.publication.year].filter(Boolean).join(' · ')||'发表信息待核实'}</strong><p>{report.publication.detail}</p><SourceLinks ids={report.publication.source_ids||[]} sources={sources}/></div></section>
      <section className="research-section"><h4>作者与机构</h4><p className="small muted">结合本文题目与署名查作者身份、机构关系及 GitHub 主页，附可核对的公开来源。</p>{report.authors.length?report.authors.map((author,i)=><article className="research-entry" key={i}><strong>{author.name}</strong>{author.role&&<span className="research-role">{author.role}</span>}{author.affiliation&&<p className="author-affiliation">{author.affiliation}</p>}{author.detail&&<p>{author.detail}</p>}<SourceLinks ids={author.source_ids||[]} sources={sources}/><button className="research-deep" disabled={lookupRunning} onClick={()=>void lookup('author',author.name)}>查身份、关系与 GitHub</button>{author.affiliation.trim()&&<button className="research-deep" title={'查证机构：'+author.affiliation.trim().slice(0,240)} aria-label={'查机构背景：'+author.affiliation.trim().slice(0,240)} disabled={lookupRunning||author.affiliation.trim().length<2} onClick={()=>void lookup('institution',author.affiliation)}>查机构背景</button>}{newestLookups(lookups,'author').filter(([,entry])=>normalizeQuery(entry.query)===normalizeQuery(author.name)).map(([key,entry])=><LookupCard key={key} entry={entry} disabled={lookupRunning} onRetry={()=>void lookup('author',entry.query)}/>)}</article>):<p className="small muted">暂未找到足够可靠的作者背景资料。</p>}</section>
      <section className="research-section"><h4>研究意义</h4>{report.significance.map((item,i)=><article className="research-entry" key={i}><span className={'research-badge '+item.kind}>{kindNames[item.kind]||'综合分析'}</span><strong>{item.title}</strong><p>{item.detail}</p><SourceLinks ids={item.source_ids||[]} sources={sources}/></article>)}</section>
      {!!report.limitations.length&&<section className="research-section research-limitations"><h4>仍需核实</h4><ul>{report.limitations.map((item,i)=><li key={i}>{item}</li>)}</ul></section>}
      <details className="research-sources" open><summary>来源资料 · {sources.length}</summary>{sources.map(source=>{const url=safeURL(source.url);return url?<a key={source.id} href={url} target="_blank" rel="noopener noreferrer" title={sourceDomain(url)}><span>{source.title}<small className="lookup-domain">{sourceDomain(url)}</small></span><ArrowUpRight size={14}/></a>:<p className="small muted" key={source.id}>{source.title}（链接不可用）</p>;})}</details>
    </>}
    {!!newestLookups(lookups,'institution').length&&<section className="research-section"><h4>机构背景查证</h4><p className="small muted">按机构汇总公开背景、研究方向及有来源的合作关系。</p>{newestLookups(lookups,'institution').map(([key,entry])=><LookupCard key={key} entry={entry} disabled={lookupRunning} onRetry={()=>void lookup('institution',entry.query)}/>)}</section>}
    <section className="research-section"><h4>相关与后续工作</h4><p className="small muted">从这篇论文出发查找相关研究，并说明关联理由；后续引用关系需要来源明确证明。</p><button className="research-deep" disabled={lookupRunning||paperTitle.trim().length<2} onClick={()=>void lookup('related',paperTitle)}>{lookupRunning?'正在查证…':'联网查找相关工作'}</button>{newestLookups(lookups,'related').map(([key,entry])=><LookupCard key={key} entry={entry} disabled={lookupRunning} onRetry={()=>void lookup('related',entry.query)}/>)}</section>
    {!!research?.queries?.length&&<details className="research-queries"><summary>检索线索</summary>{research.queries.map((query,i)=><p key={i}>{query}</p>)}</details>}
  </div>;
}
