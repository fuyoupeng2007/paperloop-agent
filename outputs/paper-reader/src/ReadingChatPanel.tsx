import React,{useEffect,useRef,useState,type RefObject} from 'react';
import {ArrowUpRight,BookOpen,CircleAlert,LoaderCircle,MessageCircle,ScanLine,Send,Sparkles,StickyNote,X} from 'lucide-react';

export type Citation={block_id:string;page:number;quote:string;stale?:boolean};
export type Chat={id:string;question:string;answer:string;supplement:string;citations:Citation[];coverage:string;visual_page:number|null;visual_box?:number[]|null;anchor_id?:string|null;selected_text?:string;context_key?:string;scope?:string;block_id?:string|null;page?:number|null};
export type AskContext={scope:string;block_id:string|null;page:number;selection:string;box:number[]|null;anchor_id?:string|null};

function AnswerText({chat}:{chat:Chat}){let value=chat.answer;chat.citations.forEach((ref,i)=>{value=value.split('['+ref.block_id+']').join('【原文 '+(i+1)+'】');});return <p>{value.split(/(\*\*[^*]+\*\*)/g).map((part,i)=>part.startsWith('**')&&part.endsWith('**')?<strong key={i}>{part.slice(2,-2)}</strong>:<React.Fragment key={i}>{part}</React.Fragment>)}</p>;}
function historyLabel(chat:Chat){const key=chat.context_key||'paper';return key==='paper'?'整篇论文':key.startsWith('object:')?'图表 · p.'+(chat.visual_page||chat.page||'—'):key.startsWith('region:')?'框选区域 · p.'+(chat.visual_page||chat.page||'—'):key.startsWith('selection:')?(chat.selected_text?'选中文字':'段落')+' · p.'+(chat.page||chat.citations[0]?.page||'—'):key.startsWith('section:')?'章节讨论':'页面 · p.'+(chat.page||chat.visual_page||key.slice(5));}

type Props={
  chats:Chat[];contextKey:string;contextLabel:string;contextQuote:string;scope:string;hasBlock:boolean;
  page:number;region:number[]|null;question:string;asking:boolean;askingHere:boolean;error:string;
  questionRef:RefObject<HTMLTextAreaElement|null>;onScope:(value:string)=>void;onQuestion:(value:string)=>void;
  onWholePaper:()=>void;onClearRegion:()=>void;onSend:()=>void;onRetry:()=>void;
  onCite:(ref:Citation)=>void;onPage:(page:number)=>void;onVisual:(chat:Chat)=>void;onSave:(chat:Chat)=>void;
};

export function ReadingChatPanel(props:Props){
  const {chats,contextKey,contextLabel,contextQuote,scope,hasBlock,page,region,question,asking,askingHere,error,questionRef,onScope,onQuestion,onWholePaper,onClearRegion,onSend,onRetry,onCite,onPage,onVisual,onSave}=props;
  const [allHistory,setAllHistory]=useState(false);
  const end=useRef<HTMLDivElement>(null);
  const visible=allHistory?chats:chats.filter(chat=>(chat.context_key||'paper')===contextKey);
  useEffect(()=>setAllHistory(false),[contextKey]);
  useEffect(()=>{end.current?.scrollIntoView({behavior:'smooth',block:'nearest'});},[visible.length,askingHere,contextKey]);
  return <>
    <div className={'conversation-context '+(region?'visual-context':'')} aria-label="当前问答上下文"><div><span>{region?<ScanLine size={14}/>:scope==='paper'?<BookOpen size={14}/>:<MessageCircle size={14}/>} {contextLabel}</span>{scope!=='paper'||region?<button onClick={onWholePaper}>切到整篇</button>:null}</div>{contextQuote&&<p title={contextQuote}>{contextQuote}</p>}<small>{scope==='selection'?'接下来的追问继续围绕这段原文。':scope==='section'?'接下来的追问继续围绕当前章节。':region?'将根据框选图像与本页文字回答。':'回答会附上可回到原文核对的依据。'}</small></div>
    <div className="history-filter"><button className={!allHistory?'active':''} onClick={()=>setAllHistory(false)}>当前范围</button><button className={allHistory?'active':''} onClick={()=>setAllHistory(true)}>全部历史 <span>{chats.length}</span></button></div>
    <div className="chat-scroll" aria-live="polite">
      {visible.length===0&&<div className="assistant-intro"><span><Sparkles size={24}/></span><h3>{scope==='selection'?'把这一段读透。':'读到哪里，问到哪里。'}</h3><p>{scope==='selection'?'可以追问术语、推导过程、隐含假设，或请它举个例子。':'当前范围还没有讨论。选择段落或直接输入问题，开始精读。'}</p>{(scope==='selection'?['这一段的核心意思是什么？','请解释这里的关键术语。','能用一个具体例子说明吗？']:['这篇论文解决了什么问题？','主要实验结果和局限是什么？','请解释本文的方法步骤。']).map(q=><button key={q} disabled={asking} onClick={()=>{onQuestion(q);questionRef.current?.focus();}}>{q}<ArrowUpRight size={14}/></button>)}</div>}
      {visible.map(chat=><div className="chat-pair" key={chat.id}>{allHistory&&<div className="chat-scope-label">{historyLabel(chat)}</div>}<div className="chat-question">{chat.question}</div><div className="chat-answer"><span className="assistant-mark"><Sparkles size={14}/>PaperLoop</span><AnswerText chat={chat}/>{chat.supplement&&<details><summary>辅助解释 · 非论文结论</summary><p>{chat.supplement}</p></details>}<div className="citations">{chat.citations.map((ref,i)=><button key={i} title={ref.quote} onClick={()=>onCite(ref)}>原文 {i+1} · p.{ref.page}{ref.stale?' · 已重解析':''}</button>)}{chat.visual_page&&<button onClick={()=>chat.visual_box?.length===4?onVisual(chat):onPage(chat.visual_page!)}>图像依据 · p.{chat.visual_page}{chat.visual_box?.length===4?' · 返回选区':''}</button>}</div><small className="muted">{chat.coverage}</small><button className="save-answer" onClick={()=>onSave(chat)}><StickyNote size={13}/>保存为笔记</button></div></div>)}
      {asking&&(askingHere||allHistory)&&<p className="thinking" role="status"><LoaderCircle size={16} className="spin"/>正在阅读原文并组织回答…</p>}
      {error&&<div className="chat-error" role="alert"><CircleAlert size={16}/><div><p>{error}</p><button disabled={asking} onClick={onRetry}>重试这条问题</button></div></div>}
      <div ref={end}/>
    </div>
    <div className="composer">
      {region?<div className="context-chip"><ScanLine size={13}/>已框选第 {page} 页区域 <button onClick={onClearRegion} aria-label="取消图像选区"><X size={13}/></button></div>:<div className="scope-row"><span>提问范围</span><select aria-label="提问范围" value={scope} onChange={e=>onScope(e.target.value)}><option value="paper">整篇论文</option><option value="page">当前页面</option><option value="selection" disabled={!hasBlock}>选中段落</option><option value="section" disabled={!hasBlock}>当前章节</option></select></div>}
      <textarea ref={questionRef} aria-label="论文问题" value={question} onChange={e=>onQuestion(e.target.value)} placeholder={scope==='selection'?'继续追问这一段…':'有什么想进一步理解的？'} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();onSend();}}}/>
      <div className="composer-bottom"><small>{asking&&!askingHere?'上一条问题仍在处理中':'Ctrl + Enter 发送'}</small><button className="primary" disabled={asking||!question.trim()} onClick={onSend}>{asking?<LoaderCircle size={15} className="spin"/>:<Send size={15}/>} {asking?'正在回答':'发送'}</button></div>
    </div>
  </>;
}
