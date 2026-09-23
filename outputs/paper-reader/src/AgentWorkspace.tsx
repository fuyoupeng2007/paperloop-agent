import {useEffect,useRef,useState} from 'react';
import {ArrowUpRight,BookOpen,CheckCircle2,ChevronDown,Download,FileSearch,Globe2,Image,LoaderCircle,Pause,Play,Plus,RefreshCw,Square,X} from 'lucide-react';
import './agent.css';

type Citation={block_id:string;page:number;quote:string};
export type AgentEvidence={id:string;type:'paper'|'visual'|'web';title:string;quote:string;block_id?:string;page?:number;box?:number[];object_id?:string;url?:string};
type AgentStep={index:number;action:string;reason:string;status:'running'|'completed'|'error'|'interrupted';started_at:number;finished_at?:number;args?:{query?:string;block_ids?:string[];object_id?:string};observation?:{summary:string;evidence_ids:string[];error?:string}};
type AgentRun={id:string;doc_id:string;goal:string;status:'running'|'paused'|'completed'|'needs_attention'|'cancelled'|'error';created_at:number;updated_at:number;allow_web:boolean;max_steps:number;max_calls:number;steps_used:number;calls_used:number;plan:string[];open_questions:string[];steps:AgentStep[];evidence:AgentEvidence[];claims:{text:string;evidence_ids:string[]}[];limitations:string[];review:{goal_met:boolean;checks:{index:number;supported:boolean;reason:string}[];missing:string[]}|null;error:string;pause_requested:boolean;cancel_requested:boolean;stop_reason?:string};
type Props={documentId:string;paperTitle:string;onClose:()=>void;onCite:(reference:Citation)=>void;onVisual?:(reference:{page:number;box?:number[];object_id?:string})=>void};
const examples=[
  {label:'方法与证据',goal:'梳理这篇论文的核心方法，找出支持主要贡献的原文和图表证据，并说明证据还不能回答的问题。',web:false},
  {label:'复现条件',goal:'整理复现这篇论文需要的数据、模型、训练和评测设置、硬件与代码，逐项标明原文依据和缺失信息。',web:false},
  {label:'作者与 GitHub',goal:'核对这篇论文主要作者的身份与机构，查找有来源支持的 GitHub 个人主页和官方项目仓库，区分已核实信息与同名候选。',web:true},
];
const actionNames:Record<string,string>={deciding:'选择下一步',search_paper:'检索论文',read_blocks:'阅读原文',inspect_figure:'查看图表',web_search:'联网补证据',finish:'检查并形成结论'};
const statusNames:Record<AgentRun['status'],string>={running:'研究中',paused:'已暂停',completed:'已完成',needs_attention:'需要继续核对',cancelled:'已停止',error:'遇到问题'};
function timeLabel(value:number){return new Date(value*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function publicURL(value?:string){try{const url=new URL(value||'');return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}}
function friendlyError(value:string){const names:Record<string,string>={review_incomplete:'结论依据检查尚未通过，需要继续核对。',repeated_action:'重复查看未获得新证据，任务已停下。',provider_changed:'连接方式发生了变化，请检查连接后继续。',interrupted:'上次研究被中断，已保存的步骤可以继续。',budget:'已达到本轮研究步骤上限。'};return names[value]||(/^[a-z_]+$/.test(value)?'这一步尚未完成，请查看说明后重试。':value);}
async function api<T>(path:string,options:RequestInit={}):Promise<T>{
  const response=await fetch(path,{...options,headers:{'X-PaperLoop':'1','Content-Type':'application/json',...options.headers}});
  if(!response.ok){const body=await response.json().catch(()=>({detail:'本机服务暂时无法连接。'}));throw new Error(typeof body.detail==='string'?body.detail:'操作未完成，请重试。');}
  return response.json();
}

export function AgentWorkspace({documentId,paperTitle,onClose,onCite,onVisual}:Props){
  const [runs,setRuns]=useState<AgentRun[]>([]),[selectedId,setSelectedId]=useState(''),[loading,setLoading]=useState(true),[error,setError]=useState(''),[pollError,setPollError]=useState('');
  const [goal,setGoal]=useState(examples[0].goal),[limit,setLimit]=useState(8),[allowWeb,setAllowWeb]=useState(false),[showComposer,setShowComposer]=useState(true),[pending,setPending]=useState(''),[resumeLimit,setResumeLimit]=useState(8);
  const [webAvailable,setWebAvailable]=useState<boolean|null>(null),[capabilityNotice,setCapabilityNotice]=useState('');
  const closeRef=useRef<HTMLButtonElement>(null),dialogRef=useRef<HTMLDivElement>(null),goalRef=useRef<HTMLTextAreaElement>(null);
  const base='/api/documents/'+encodeURIComponent(documentId)+'/agent-runs',run=runs.find(value=>value.id===selectedId);
  const activeIds=runs.filter(value=>value.status==='running').map(value=>value.id).sort().join(','),hasActive=!!activeIds;
  function merge(value:AgentRun){setRuns(previous=>{const old=previous.find(item=>item.id===value.id);return old&&old.updated_at>value.updated_at?previous:[value,...previous.filter(item=>item.id!==value.id)].sort((a,b)=>b.created_at-a.created_at);});}
  async function load(signal?:AbortSignal){const values=await api<AgentRun[]>(base,{signal});setRuns(values);setSelectedId(previous=>values.some(value=>value.id===previous)?previous:values.find(value=>value.status==='running')?.id||values[0]?.id||'');return values;}
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError('');setRuns([]);setSelectedId('');
    void load(controller.signal).then(values=>setShowComposer(!values.length)).catch(e=>{if(!controller.signal.aborted)setError((e as Error).message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    setWebAvailable(null);setCapabilityNotice('');
    void api<{tools:{name:string;description:string}[];notice?:string}>('/api/documents/'+encodeURIComponent(documentId)+'/agent-capabilities',{signal:controller.signal}).then(value=>{const available=value.tools.some(tool=>tool.name==='web_search');setWebAvailable(available);setCapabilityNotice(value.notice||'');if(!available)setAllowWeb(false);}).catch(()=>{if(!controller.signal.aborted){setWebAvailable(false);setCapabilityNotice('暂时无法确认联网能力；仍可开始基于论文的研究。');}});
    return()=>controller.abort();
  },[documentId]);
  useEffect(()=>{
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.stopPropagation();onClose();}};
    window.addEventListener('keydown',escape);closeRef.current?.focus();return()=>{window.removeEventListener('keydown',escape);previous?.focus();};
  },[]);
  useEffect(()=>{if(dialogRef.current&&!dialogRef.current.contains(document.activeElement))closeRef.current?.focus();},[run?.status,showComposer]);
  useEffect(()=>{
    if(!activeIds)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    async function poll(){
      try{const values=await Promise.all(activeIds.split(',').map(id=>api<AgentRun>(base+'/'+encodeURIComponent(id),{signal:controller.signal})));if(controller.signal.aborted)return;values.forEach(merge);setPollError('');}
      catch(e){if(controller.signal.aborted)return;setPollError((e as Error).message);}
      if(!controller.signal.aborted)timer=setTimeout(()=>void poll(),1800);
    }
    timer=setTimeout(()=>void poll(),900);
    return()=>{controller.abort();clearTimeout(timer);};
  },[activeIds,base]);
  useEffect(()=>{if(run)setResumeLimit(Math.min(16,Math.max(run.max_steps,run.steps_used+4)));},[run?.id,run?.max_steps,run?.steps_used]);
  async function start(){
    if(pending||hasActive||goal.trim().length<3||limit<3||limit>16)return;
    setPending('start');setError('');
    try{const value=await api<AgentRun>(base,{method:'POST',body:JSON.stringify({goal:goal.trim(),max_steps:limit,allow_web:allowWeb})});merge(value);setSelectedId(value.id);setShowComposer(false);}
    catch(e){setError((e as Error).message);}finally{setPending('');}
  }
  async function control(action:'pause'|'resume'|'cancel'){
    if(!run||pending)return;setPending(action);setError('');
    try{merge(await api<AgentRun>(base+'/'+encodeURIComponent(run.id)+'/control',{method:'POST',body:JSON.stringify({action,...(action==='resume'?{max_steps:resumeLimit}:{})})}));}
    catch(e){setError((e as Error).message);}finally{setPending('');}
  }
  function evidenceChips(ids:string[]){return <div className="agent-evidence-chips">{ids.map(id=>{const source=run?.evidence.find(item=>item.id===id);return source?<button key={id} type="button" onClick={()=>{const element=document.getElementById('agent-evidence-'+id);if(element instanceof HTMLDetailsElement)element.open=true;element?.scrollIntoView({behavior:'smooth',block:'nearest'});}}>{source.type==='web'?<Globe2 size={11}/>:source.type==='visual'?<Image size={11}/>:<BookOpen size={11}/>} {source.type==='web'?'网页':source.page?'PDF p.'+source.page:'原文'} · {source.title}</button>:null;})}</div>;}
  function viewEvidence(source:AgentEvidence){if(!source.page)return;if(source.type==='visual'&&onVisual)onVisual({page:source.page,box:source.box,object_id:source.object_id});else onCite({block_id:source.block_id||'',page:source.page,quote:source.quote});}
  const canResume=run&&['paused','needs_attention','error'].includes(run.status),waitingBoundary=run?.status==='running'&&(run.pause_requested||run.cancel_requested);
  return <div className="agent-workspace-shade" onClick={onClose}><div className="agent-workspace" ref={dialogRef} role="dialog" aria-modal="true" aria-label="研究任务" onClick={e=>e.stopPropagation()} onKeyDown={e=>{
    if(e.key==='Escape'){e.stopPropagation();onClose();}
    if(e.key==='Tab'){const elements=Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary')||[]).filter(element=>element.getClientRects().length),first=elements[0],last=elements[elements.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  }}>
    <header className="agent-workspace-head"><div><span className="agent-eyebrow"><FileSearch size={14}/> PAPERLOOP · 研究助手</span><h2>研究任务</h2><p title={paperTitle}>{paperTitle}</p></div><button ref={closeRef} className="icon" onClick={onClose} aria-label="关闭研究任务"><X size={21}/></button></header>
    <div className="agent-workspace-body"><aside className="agent-history" aria-label="已保存的研究任务"><button className="secondary" onClick={()=>{setShowComposer(true);setError('');requestAnimationFrame(()=>goalRef.current?.focus());}}><Plus size={14}/>新建任务</button><h3>本篇任务 · {runs.length}</h3>{loading&&<p role="status">正在读取本机任务…</p>}{!loading&&!runs.length&&<p>开始一次研究后，目标、步骤和证据会保存在这里。</p>}{runs.map(item=><button key={item.id} className={'agent-history-item '+(item.id===selectedId?'selected':'')} aria-pressed={item.id===selectedId} onClick={()=>{setSelectedId(item.id);setShowComposer(false);setError('');}}><strong>{item.goal}</strong><span><i className={'agent-status-dot '+item.status}/>{statusNames[item.status]} · {item.steps_used}/{item.max_steps} 步</span><small>{timeLabel(item.created_at)}</small></button>)}<p className="agent-history-note">关闭窗口后，进行中的任务会继续。重新打开可查看进展。</p></aside>
      <div className="agent-main"><p className="agent-intro">给出一个阅读目标，助手会根据已找到的证据，自行决定下一步检索、阅读、查看图表或联网查证，再检查结论是否有依据。</p>
        {error&&<div className="agent-error" role="alert">{error}<button onClick={()=>{setError('');void load().catch(e=>setError((e as Error).message));}}><RefreshCw size={12}/>刷新任务</button></div>}
        {pollError&&<p className="agent-error" role="status">进度更新暂时中断：{pollError} 已保存的步骤仍可阅读，正在尝试重新连接。</p>}
        {showComposer&&<form className="agent-composer" onSubmit={e=>{e.preventDefault();void start();}}><div className="agent-section-head"><h3>这次想弄清什么？</h3>{run&&<button type="button" className="icon" aria-label="收起新任务" onClick={()=>setShowComposer(false)}><ChevronDown size={17}/></button>}</div><div className="agent-goal-examples">{examples.map(example=><button type="button" key={example.label} onClick={()=>{setGoal(example.goal);setAllowWeb(example.web&&webAvailable===true);goalRef.current?.focus();}}>{example.label}</button>)}</div><label className="agent-field" htmlFor="agent-goal">研究目标<textarea ref={goalRef} id="agent-goal" value={goal} maxLength={2000} rows={4} onChange={e=>setGoal(e.target.value)} placeholder="例如：核对论文的主要结论及支持它们的实验依据"/></label><div className="agent-composer-options"><label>最多研究步骤<select aria-label="最多研究步骤" value={limit} onChange={e=>setLimit(Number(e.target.value))}>{Array.from({length:14},(_,i)=>i+3).map(value=><option key={value} value={value}>{value} 步</option>)}</select></label><label className="agent-web-toggle"><input type="checkbox" disabled={webAvailable!==true} checked={allowWeb} onChange={e=>setAllowWeb(e.target.checked)}/><Globe2 size={14}/>允许联网查证</label></div><p className="agent-help">研究步骤包括查找、阅读、查证与结论检查；达到上限时会停下，你可以追加步骤。任务只读取论文与公开资料，保留现有译文和笔记。</p>{webAvailable===false&&<p className="agent-help">{capabilityNotice||'当前连接支持论文内阅读，暂时无法联网查证。'}</p>}{hasActive&&<p className="agent-help">这篇论文已有任务在进行，完成或暂停后可以开始新任务。</p>}<button className="primary" type="submit" disabled={loading||!!pending||hasActive||goal.trim().length<3}>{pending==='start'?<LoaderCircle size={15} className="spin"/>:<Play size={15}/>}开始研究</button></form>}
        {run&&<article className="agent-run"><div className="agent-run-heading"><div><span className={'agent-status '+run.status}>{run.status==='running'&&<LoaderCircle size={13} className="spin"/>}{run.cancel_requested?'正在结束当前步骤':run.pause_requested?'等待当前步骤暂停':statusNames[run.status]}</span><h3>{run.goal}</h3><p>{run.steps_used}/{run.max_steps} 步 · {run.allow_web?'已允许联网':'仅依据当前论文'} · {run.evidence.length} 条证据</p></div><div className="agent-run-controls">{run.status==='running'&&<><button className="secondary" disabled={!!pending||!!waitingBoundary} onClick={()=>void control('pause')}><Pause size={13}/>暂停</button><button className="secondary" disabled={!!pending||run.cancel_requested} onClick={()=>void control('cancel')}><Square size={12}/>停止任务</button></>}<details className="agent-export"><summary><Download size={13}/>导出记录</summary><a href={base+'/'+encodeURIComponent(run.id)+'/export?format=md'} download>Markdown 文档</a><a href={base+'/'+encodeURIComponent(run.id)+'/export?format=html'} target="_blank" rel="noopener noreferrer">阅读报告 / 打印 PDF</a></details></div></div>
          {waitingBoundary&&<p className="agent-notice" role="status">{run.cancel_requested?'停止':'暂停'}请求已收到。正在进行的查看或查询返回后就会停下，已找到的证据会保留。</p>}
          {run.error&&<p className="agent-error" role="alert">{friendlyError(run.error)}</p>}
          {canResume&&<div className="agent-resume"><div><strong>{run.stop_reason==='budget'?'已达到本次步骤上限':run.status==='paused'?'任务已暂停':'还有内容需要核对'}</strong><p>{run.steps_used>=16?'已用满 16 步。可以根据已有结论缩小目标，再建立一个新任务。':'保留已有证据，继续完成剩余问题。'}</p></div>{run.steps_used<16&&<><label>本轮总步骤<select aria-label="继续后的总步骤" value={resumeLimit} onChange={e=>setResumeLimit(Number(e.target.value))}>{Array.from({length:14},(_,i)=>i+3).filter(value=>value>=run.max_steps&&value>run.steps_used).map(value=><option key={value} value={value}>{value} 步</option>)}</select></label><button className="primary" disabled={!!pending||resumeLimit<=run.steps_used} onClick={()=>void control('resume')}><Play size={13}/>继续研究</button></>}</div>}
          {!!run.plan.length&&<section className="agent-plan"><h4>当前计划</h4><ol>{run.plan.map((item,index)=><li key={index}>{item}</li>)}</ol><small>计划会根据新证据调整；下面记录每一步做了什么、为何需要它以及找到的结果。</small></section>}
          <section className="agent-timeline" aria-label="研究步骤"><h4>研究过程</h4>{!run.steps.length&&<p className="agent-help">{run.status==='running'?'正在根据目标制定第一步…':'本次尚未产生研究步骤。'}</p>}{run.steps.map((step,index)=><article key={step.index+'-'+index} className={'agent-step '+step.status}><span className="agent-step-marker">{step.status==='running'?<LoaderCircle size={14} className="spin"/>:step.status==='completed'?<CheckCircle2 size={14}/>:index+1}</span><div><div className="agent-step-head"><strong>{actionNames[step.action]||'核对证据'}</strong><small>{step.status==='running'?'进行中':step.status==='error'?'未完成':step.status==='interrupted'?'已中断':'已记录'}</small></div>{step.reason&&<p className="agent-step-reason">目的：{step.reason}</p>}{step.args?.query&&<p className="agent-search-query">查询：{step.args.query}</p>}{step.observation?.summary&&<p>{step.observation.summary}</p>}{step.observation?.error&&<p className="agent-error">{friendlyError(step.observation.error)}</p>}{evidenceChips(step.observation?.evidence_ids||[])}</div></article>)}</section>
          {!!run.claims.length&&<section className="agent-conclusions"><div className="agent-section-head"><h4>{run.status==='completed'?'研究结论':'已得到的结论'}</h4>{run.review&&<span className={'agent-review-badge '+(run.review.goal_met?'passed':'')}>{run.review.goal_met?'目标检查通过':'仍有待核对问题'}</span>}</div>{run.claims.map((claim,index)=><article className="agent-claim" key={index}><p>{claim.text}</p>{evidenceChips(claim.evidence_ids)}</article>)}</section>}
          {run.review&&!!run.review.checks.length&&<details className="agent-review"><summary>结论依据检查 · {run.review.checks.filter(check=>check.supported).length}/{run.review.checks.length} 项得到支持</summary>{run.review.checks.map((check,index)=><p key={index}><span>{check.supported?'有依据':'待核对'}</span>{check.reason}</p>)}</details>}
          {(!!run.open_questions.length||!!run.limitations.length||!!run.review?.missing.length)&&<section className="agent-uncertainty"><h4>尚未解决与证据限制</h4><ul>{[...new Set([...run.open_questions,...run.limitations,...(run.review?.missing||[])])].map((item,index)=><li key={index}>{item}</li>)}</ul></section>}
          {!!run.evidence.length&&<section className="agent-sources"><h4>本次证据 · {run.evidence.length}</h4>{run.evidence.map(source=>{const url=source.type==='web'?publicURL(source.url):null;return <details key={source.id} id={'agent-evidence-'+source.id} className="agent-evidence"><summary><span className={'agent-source-kind '+source.type}>{source.type==='web'?'公开网页':source.type==='visual'?'图表观察':'PDF 原文'}</span>{source.title}{source.page?' · p.'+source.page:''}</summary><blockquote>{source.quote||'查看来源以核对具体内容。'}</blockquote>{source.type==='visual'&&<p className="agent-help">这是模型对图表的观察，具体数值与结论请回原图核对。</p>}{source.type==='web'?(url?<a href={url} target="_blank" rel="noopener noreferrer">打开来源 · {new URL(url).hostname}<ArrowUpRight size={12}/></a>:<small>来源链接不可用</small>):source.page?<button className="text-button" onClick={()=>viewEvidence(source)}>{source.type==='visual'?'查看原图区域':'回到原文段落'}<ArrowUpRight size={12}/></button>:<small>未记录可定位的页码</small>}</details>;})}</section>}
        </article>}
      </div>
    </div>
  </div></div>;
}
