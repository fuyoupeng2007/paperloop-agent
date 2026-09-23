import {useEffect,useRef,useState} from 'react';
import {Download,LoaderCircle,MessageCircle,Send,X} from 'lucide-react';

type Summary={id:string;title:string;page_count:number};
type Citation={document_id:string;block_id:string;page:number;quote:string};
type Cell=Citation&{claim:string;evidence_scope:string};
type ComparisonChat={id:string;question:string;answer:string;supplement?:string;citations:Citation[];coverage?:string;created_at:number};
type Comparison={id:string;created_at?:number;papers:{id:string;title:string}[];rows:{dimension:string;cells:Cell[]}[];summary:string;limitations:string[];chats?:ComparisonChat[]};
const dimensions=['研究问题','输入与输出','核心方法','训练或评测数据','关键指标','实验条件','速度与硬件','局限','代码或模型可用性'];

async function api<T>(path:string,options:RequestInit={}):Promise<T>{
  const response=await fetch('/api'+path,{...options,headers:{'X-PaperLoop':'1','Content-Type':'application/json',...options.headers}});
  if(!response.ok){const value=await response.json().catch(()=>({detail:'服务暂不可用'}));throw new Error(typeof value.detail==='string'?value.detail:'操作未完成，请重试。');}
  return response.json();
}
function timestamp(value?:number){return value?new Date(value*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';}

export function ComparisonWorkspace({papers,onClose,onOpenPaper}:{papers:Summary[];onClose:()=>void;onOpenPaper:(id:string,page:number,blockId?:string,quote?:string)=>void}){
  const [chosen,setChosen]=useState<string[]>([]),[fields,setFields]=useState<string[]>(dimensions);
  const [results,setResults]=useState<Comparison[]>([]),[current,setCurrent]=useState<Comparison|null>(null);
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [question,setQuestion]=useState(''),[asking,setAsking]=useState(false),[askError,setAskError]=useState('');
  const closeRef=useRef<HTMLButtonElement>(null),dialogRef=useRef<HTMLDivElement>(null),answerRef=useRef<HTMLDivElement>(null);
  const pending=busy||asking;
  function useCriteria(result:Comparison){setChosen(result.papers.filter(p=>papers.some(item=>item.id===p.id)).map(p=>p.id).slice(0,5));setFields(dimensions.filter(value=>result.rows.some(row=>row.dimension===value)));}
  useEffect(()=>{
    let active=true;
    void api<Comparison[]>('/comparisons').then(values=>{if(active){setResults(values);setCurrent(values[0]||null);if(values[0])useCriteria(values[0]);}}).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setLoading(false);});
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    closeRef.current?.focus();
    return()=>{active=false;previous?.focus();};
  },[]);
  async function compare(){
    if(loading||pending||chosen.length<2||!fields.length)return;
    setBusy(true);setError('');setAskError('');
    try{
      const result=await api<Comparison>('/comparisons',{method:'POST',body:JSON.stringify({document_ids:chosen,dimensions:fields})});
      setCurrent(result);setQuestion('');setResults(previous=>[result,...previous.filter(item=>item.id!==result.id)]);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function ask(){
    const prompt=question.trim(),comparison=current;
    if(!comparison||pending||!prompt)return;
    setAsking(true);setAskError('');
    try{
      const chat=await api<ComparisonChat>('/comparisons/'+comparison.id+'/ask',{method:'POST',body:JSON.stringify({question:prompt})});
      const update=(item:Comparison)=>item.id===comparison.id?{...item,chats:[...(item.chats||[]),chat]}:item;
      setCurrent(previous=>previous?update(previous):null);setResults(previous=>previous.map(update));setQuestion('');
      requestAnimationFrame(()=>answerRef.current?.scrollIntoView({behavior:'smooth',block:'nearest'}));
    }catch(e){setAskError((e as Error).message);}finally{setAsking(false);}
  }
  function openSaved(result:Comparison){setCurrent(result);useCriteria(result);setQuestion('');setAskError('');}
  function openEvidence(reference:Citation){
    if(!papers.some(p=>p.id===reference.document_id)){setError('这条证据对应的论文已不在书架中，仍可查看和导出保存的原文摘录。');return;}
    onOpenPaper(reference.document_id,reference.page,reference.block_id,reference.quote);
  }
  const evidenceCount=current?.rows.reduce((count,row)=>count+row.cells.filter(cell=>cell.page>0&&cell.quote).length,0)||0;
  const cellCount=current?.rows.reduce((count,row)=>count+row.cells.length,0)||0;
  return <div className="comparison-shade" onClick={onClose}>
    <div ref={dialogRef} className="comparison-workspace" role="dialog" aria-modal="true" aria-label="跨论文比较" onClick={e=>e.stopPropagation()} onKeyDown={e=>{
      if(e.key==='Escape'){e.stopPropagation();onClose();}
      if(e.key==='Tab'){
        const elements=Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),summary')||[]).filter(element=>element.getClientRects().length);
        const first=elements[0],last=elements[elements.length-1];
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
      }
    }}>
      <header><div><h2>跨论文比较</h2><p>选择 2–5 篇本机论文，逐项比较并追问差异。比较表与问答会自动保存在本机。</p></div><button ref={closeRef} className="icon" onClick={onClose} aria-label="关闭"><X size={20}/></button></header>
      <div className="comparison-setup">
        <div><strong>选择论文 · {chosen.length}/5</strong><div className="comparison-papers">{papers.map(p=><label key={p.id}><input type="checkbox" checked={chosen.includes(p.id)} disabled={pending||(!chosen.includes(p.id)&&chosen.length>=5)} onChange={()=>setChosen(previous=>previous.includes(p.id)?previous.filter(x=>x!==p.id):[...previous,p.id])}/><span>{p.title}</span><small>{p.page_count} 页</small></label>)}</div>{papers.length<2&&<p>书架目前不足两篇；导入另一篇后即可生成比较表。</p>}</div>
        <div><strong>比较维度 · {fields.length} 项</strong><div className="comparison-fields">{dimensions.map(value=><label key={value}><input type="checkbox" checked={fields.includes(value)} disabled={pending} onChange={()=>setFields(previous=>previous.includes(value)?previous.filter(x=>x!==value):dimensions.filter(x=>previous.includes(x)||x===value))}/>{value}</label>)}</div><button className="primary" disabled={loading||pending||chosen.length<2||!fields.length} onClick={()=>void compare()}>{busy?<LoaderCircle size={15} className="spin"/>:null}{busy?'正在提取证据并比较…':'生成比较表'}</button><p>依据已解析的 PDF 段落抽取证据；未找到证据不代表原论文没有报告。不同实验条件下的数值需要分别判断。</p>{current&&<p>调整上方的论文或维度可生成新比较，已有结果会保留。</p>}{busy&&<p role="status">正在整理所选论文，完成后自动保存。你可以继续等待，或关闭窗口后从已保存的比较中查看。</p>}</div>
      </div>
      {error&&<p className="error-inline" role="alert">{error}</p>}
      {loading&&<p className="small muted" role="status">正在读取本机比较记录…</p>}
      {!!results.length&&<div className="comparison-history"><strong>已保存的比较 · {results.length}</strong>{results.map(result=><button key={result.id} disabled={pending} className={current?.id===result.id?'selected':''} aria-pressed={current?.id===result.id} title={result.papers.map(p=>p.title).join(' · ')} onClick={()=>openSaved(result)}>{timestamp(result.created_at)} {result.papers.map(p=>p.title).join(' · ')}</button>)}</div>}
      {current&&<div className="comparison-result">
        <div className="comparison-result-head"><h3>比较结果</h3><a href={'/api/comparisons/'+current.id+'/export'} download><Download size={15}/>导出比较与问答</a></div>
        <p className="comparison-coverage">{timestamp(current.created_at)} · {current.papers.length} 篇论文 · {evidenceCount}/{cellCount} 格有可定位的原文摘录</p>
        <div className="comparison-table-wrap"><table><thead><tr><th scope="col">维度</th>{current.papers.map(p=><th scope="col" key={p.id}>{p.title}</th>)}</tr></thead><tbody>{current.rows.map(row=><tr key={row.dimension}><th scope="row">{row.dimension}</th>{current.papers.map(paper=>{const cell=row.cells.find(item=>item.document_id===paper.id);return <td key={paper.id}>{cell?<><p>{cell.claim}</p>{cell.page>0&&cell.quote?<><button onClick={()=>openEvidence(cell)}>PDF p.{cell.page} · 查看原文</button><details><summary>原文证据</summary><blockquote>{cell.quote}</blockquote></details></>:<small>当前摘录中未核实</small>}</>:<small>未找到可核对证据</small>}</td>;})}</tr>)}</tbody></table></div>
        <h4>综合说明</h4><p>{current.summary}</p>
        {!!current.limitations.length&&<><h4>可比性与证据限制</h4><ul>{current.limitations.map((x,i)=><li key={i}>{x}</li>)}</ul></>}
        <section className="comparison-followup" aria-label="比较追问"><h4><MessageCircle size={15}/>围绕这组论文追问</h4><p className="small muted">例如：两篇论文的评测条件是否相同？速度差异能从哪些实验条件解释？回答会附可核对的原文摘录。</p>
          {(current.chats||[]).map(chat=><article className="comparison-chat" key={chat.id}><strong>{chat.question}</strong><p>{chat.answer}</p>{chat.supplement&&<p className="small muted">{chat.supplement}</p>}{chat.coverage&&<small>{chat.coverage}</small>}<div className="comparison-chat-citations">{chat.citations.map((citation,i)=><details key={i}><summary>{current.papers.find(p=>p.id===citation.document_id)?.title||'原文证据'} · PDF p.{citation.page}</summary><blockquote>{citation.quote}</blockquote><button className="text-button" onClick={()=>openEvidence(citation)}>查看对应 PDF 原文</button></details>)}</div></article>)}
          <div ref={answerRef}/>{askError&&<p className="error-inline" role="alert">{askError} 问题已保留，可以重新发送。</p>}{asking&&<p className="research-running" role="status">正在核对这组论文的证据并回答…</p>}
          <form className="comparison-question" onSubmit={e=>{e.preventDefault();void ask();}}><label htmlFor="comparison-question">你的问题</label><textarea id="comparison-question" value={question} disabled={pending} maxLength={2000} rows={3} placeholder="输入你想比较或继续追问的问题…" onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();void ask();}}}/><button className="primary" disabled={pending||!question.trim()} type="submit">{asking?<LoaderCircle size={15} className="spin"/>:<Send size={15}/>} {asking?'正在回答…':'发送追问'}</button><small>Ctrl / ⌘ + Enter 发送 · 追问只关联当前这组比较</small></form>
        </section>
      </div>}
    </div>
  </div>;
}
