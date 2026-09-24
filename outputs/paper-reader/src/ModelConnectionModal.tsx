import {useEffect,useRef,useState,type FormEvent} from 'react';
import {ArrowUpRight,Check,KeyRound,LoaderCircle,Sparkles,X} from 'lucide-react';

type Engine={available:boolean;logged_in:boolean;message:string};
type Config={provider?:'codex'|'api';engine?:Engine;base_url:string;model:string;vision_model:string;has_key:boolean;configured:boolean;call_limit:number;input_price:number;output_price:number};
type Props={config:Config;onClose:()=>void;onSaved:()=>void};

async function request<T>(path:string,body?:unknown):Promise<T>{
  const response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'X-PaperLoop':'1','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const value=await response.json().catch(()=>({detail:'本机服务暂时不可用。'}));
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'操作失败，请稍后重试。');
  return value as T;
}
async function saveSettings(body:unknown){
  const response=await fetch('/api/settings',{method:'PUT',headers:{'X-PaperLoop':'1','Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json().catch(()=>({detail:'保存失败。'}));
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'保存失败。');
}

export function ModelConnectionModal({config,onClose,onSaved}:Props){
  const dialog=useRef<HTMLDivElement>(null);
  const [engine,setEngine]=useState<Engine|undefined>(config.engine);
  const [baseUrl,setBaseUrl]=useState(config.base_url||'https://api.deepseek.com');
  const [model,setModel]=useState(config.model||'deepseek-flash');
  const [visionModel,setVisionModel]=useState(config.vision_model||'deepseek-flash');
  const [key,setKey]=useState('');
  const [clearKey,setClearKey]=useState(false);
  const [limit,setLimit]=useState(config.call_limit||500);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
  const official=(()=>{try{return new URL(baseUrl.trim()).hostname==='api.deepseek.com';}catch{return false;}})();
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;dialog.current?.querySelector<HTMLInputElement>('input[type="password"]')?.focus();return()=>previous?.focus();},[]);
  useEffect(()=>{let active=true;void request<Engine>('/engine/status').then(value=>{if(active)setEngine(value);}).catch(()=>{});return()=>{active=false;};},[]);

  async function useApi(event:FormEvent){
    event.preventDefault();if(busy)return;setBusy(true);setError('');setMessage('');
    try{
      const settings={provider:'api',base_url:baseUrl.trim(),model:model.trim(),vision_model:visionModel.trim(),call_limit:limit,api_key:clearKey?'':key.trim()||null};
      if(!clearKey)await request('/settings/check',settings);
      await saveSettings(settings);
      onSaved();
      if(clearKey){setKey('');setClearKey(false);setMessage('密钥已从本机删除。');}
      else onClose();
    }catch(cause){setError((cause as Error).message);}finally{setBusy(false);}
  }
  async function useCodex(action:'check'|'login'|'switch'){
    setBusy(true);setError('');setMessage('');
    try{
      if(action==='login')setMessage((await request<{message:string}>('/engine/login',{})).message);
      else{
        await saveSettings({provider:'codex'});
        if(action==='check'){
          const result=await request<{ok:boolean;message:string}>('/engine/check',{});
          if(!result.ok)throw new Error(result.message);
          setMessage(result.message);
        }else setMessage('已切换到本机 ChatGPT 登录。');
      }
      setEngine(await request<Engine>('/engine/status'));
      onSaved();
    }catch(cause){setError((cause as Error).message);}finally{setBusy(false);}
  }
  return <div className="modal-shade" onClick={()=>{if(!busy)onClose();}}><div ref={dialog} className="modal connection-modal" role="dialog" aria-modal="true" aria-label="模型连接" onClick={event=>event.stopPropagation()} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();if(!busy)onClose();}if(event.key==='Tab'){const nodes=Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),summary')||[]).filter(node=>node.getClientRects().length>0),first=nodes[0],last=nodes.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}}}>
    <div className="modal-title"><div><p className="eyebrow">START READING</p><h2>连接你的论文助手</h2></div><button className="icon" aria-label="关闭设置" disabled={busy} onClick={onClose}><X/></button></div>
    <p className="muted">安装后只需填写自己的 API 密钥。论文和阅读记录保存在这台电脑；翻译、图表理解与问答会把相关内容发送给你选择的模型服务。</p>
    <div className="connection-card"><span className="connection-icon"><KeyRound size={23}/></span><div><strong>{config.provider==='api'&&config.configured?'API 已配置':official?'使用 DeepSeek API':'使用自定义模型服务'}</strong><p>{official?'服务地址和文字、视觉模型已预填，可直接连接。':'请填写所选服务的地址与模型名；本地服务可按需留空密钥。'}</p></div>{config.provider==='api'&&config.configured&&<Check size={20}/>}</div>
    <form onSubmit={event=>void useApi(event)}>
      <label>{official?'我的 DeepSeek API 密钥':'模型服务 API 密钥'} <small>{config.has_key?'已保存；留空表示继续使用':official?'需要到 DeepSeek 平台创建自己的密钥':'服务不要求密钥时可留空'}</small><input type="password" aria-label="API 密钥" autoComplete="new-password" value={key} onChange={event=>setKey(event.target.value)} placeholder="粘贴你的 API Key"/></label>
      {config.has_key&&<label className="check-line"><input type="checkbox" checked={clearKey} onChange={event=>setClearKey(event.target.checked)}/>删除本机已保存的密钥</label>}
      <details className="advanced-settings"><summary>服务与模型设置</summary><p className="small muted">默认适用 DeepSeek。只有使用其他兼容 Chat Completions 的服务时才需要修改。</p>
        <label>服务地址<input aria-label="服务地址" value={baseUrl} onChange={event=>setBaseUrl(event.target.value)} placeholder="https://api.deepseek.com"/></label>
        <div className="form-row"><label>文字模型<input aria-label="文字模型" value={model} onChange={event=>setModel(event.target.value)}/></label><label>视觉模型<input aria-label="视觉模型" value={visionModel} onChange={event=>setVisionModel(event.target.value)}/></label></div>
        <label>每篇最多调用次数<input aria-label="每篇最多调用次数" type="number" min="1" max="10000" value={limit} onChange={event=>setLimit(Number(event.target.value))}/></label>
        {!official&&<p className="small muted">此地址由你选择；请核对服务商要求的模型名和密钥。</p>}
      </details>
      <button className="primary full" disabled={busy||(!clearKey&&(!baseUrl.trim()||!model.trim()||(official&&!key.trim()&&!config.has_key)))}>{busy?<LoaderCircle size={16} className="spin"/>:<Check size={16}/>} {clearKey?'删除密钥':'测试连接并保存'}</button>
    </form>
    {(engine?.available||config.provider==='codex')&&<div className="alternate-connection"><div className="connection-card"><span className="connection-icon"><Sparkles size={22}/></span><div><strong>本机 ChatGPT 登录</strong><p>{engine?.message||'检测本机 Codex 登录状态。'}</p></div></div><div className="connection-actions"><button className="secondary" disabled={busy} onClick={()=>void useCodex('check')}>检查连接</button>{engine?.logged_in?<button className="secondary" disabled={busy} onClick={()=>void useCodex('switch')}>使用 ChatGPT</button>:<button className="secondary" disabled={busy} onClick={()=>void useCodex('login')}>登录 ChatGPT <ArrowUpRight size={14}/></button>}</div></div>}
    <p className="connection-hint">API 密钥由 Windows 当前用户加密保存；安装包不包含任何人的密钥。DeepSeek 模式目前支持论文内研究和图表理解，联网查证工具仍需后续对接搜索服务。</p>
    {error&&<p className="error-inline" role="alert">{error}</p>}{message&&<p className="connection-message" role="status">{message}</p>}
  </div></div>;
}
