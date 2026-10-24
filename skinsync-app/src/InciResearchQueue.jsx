import { useEffect, useState } from 'react';
import { apiFetch } from './apiFetch';

const BASE='http://127.0.0.1:8000/admin/inci-research';
const input='w-full rounded-lg border border-gray-300 bg-white p-2 text-sm';
const button='rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50';
const pct=(n)=>typeof n==='number'?(n*100).toFixed(1)+'%':'—';
const statuses=['','queued','processing','needs_review','no_result','error','approved','rejected'];
async function request(path,body) {
  const res=await apiFetch(BASE+path,body===undefined?undefined:{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
  });
  const data=await res.json().catch(()=>({}));
  if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'Request failed');
  return data;
}
export default function InciResearchQueue({onClose}) {
  const [data,setData]=useState({counts:{},items:[],total:0});
  const [filter,setFilter]=useState('needs_review');
  const [search,setSearch]=useState('');
  const [minConfidence,setMinConfidence]=useState(0);
  const [page,setPage]=useState(0);
  const [run,setRun]=useState(null);
  const [runLimit,setRunLimit]=useState(10);
  const [maxPages,setMaxPages]=useState(3);
  const [reviewThreshold,setReviewThreshold]=useState(0.85);
  const [connection,setConnection]=useState(null);
  const [selected,setSelected]=useState(null);
  const [reason,setReason]=useState('');
  const [regionApproved,setRegionApproved]=useState(false);
  const [correction,setCorrection]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const query=new URLSearchParams({limit:'30',offset:String(page*30),min_confidence:String(minConfidence)});
  if(filter)query.set('status',filter);
  if(search.trim())query.set('search',search.trim());
  async function refresh() {
    try {
      const [list,conn]=await Promise.all([request('/queue?'+query),request('/connection')]);
      setData(list);setConnection(conn);
    }catch(e){setError(e.message)}
  }
  useEffect(()=>{
    let active=true;
    const update=async()=>{
      try{
        const list=await request('/queue?'+query);
        if(active)setData(list);
        if(run){
          const state=await request('/runs/'+encodeURIComponent(run.id));
          if(active)setRun(state);
        }
      }catch(e){if(active)setError(e.message)}
    };
    update();request('/connection').then(x=>{if(active)setConnection(x)}).catch(()=>{});
    const timer=setInterval(update,2500);
    return ()=>{active=false;clearInterval(timer)};
  },[filter,search,minConfidence,page,run?.id,run?.status]);
  async function operate(action) {
    setBusy(true);setError('');setMessage('');
    try {
      const response=await action();
      if(response?.status)setMessage('Operation: '+response.status);
      await refresh();
    }catch(e){setError(e.message)}
    finally{setBusy(false)}
  }
  const details=selected?data.items.find(x=>x.canonical_product_id===selected.canonical_product_id)||selected:null;
  const total=Object.values(data.counts||{}).reduce((a,b)=>a+b,0);
  return <div className="fixed inset-0 z-[70] bg-gray-950/60 p-2 sm:p-5 flex items-center justify-center">
    <div className="w-full max-w-6xl max-h-[94vh] bg-white rounded-2xl flex flex-col shadow-2xl overflow-hidden">
      <div className="p-4 border-b flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-black text-gray-900">Gemini + DonSeTch INCI research</h2>
          <p className="text-xs text-gray-600">Separate from product-identity Gemini. Search and extract source evidence only; every formula requires review and existing publication gates.</p>
        </div>
        <button type="button" className={button} onClick={onClose}>Close</button>
      </div>
      <div className="overflow-y-auto p-4 sm:p-5 space-y-4">
        {(error||message)&&<p role="status" className={'p-3 text-sm border rounded-lg '+(error?'text-red-700 border-red-200':'text-gray-700 border-gray-200')}>{error||message}</p>}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
          {statuses.slice(1).map(s=><button type="button" key={s} onClick={()=>{setFilter(s);setPage(0);}}
            className={'p-3 border rounded-xl text-left '+(filter===s?'border-purple-500 bg-purple-50':'bg-white')}>
            <span className="text-xl font-bold block">{data.counts?.[s]||0}</span><span className="text-xs">{s.replaceAll('_',' ')}</span>
          </button>)}
          <button type="button" onClick={()=>{setFilter('');setPage(0);}}
            className={'p-3 border rounded-xl text-left '+(!filter?'border-purple-500 bg-purple-50':'bg-white')}>
            <span className="text-xl font-bold block">{total}</span><span className="text-xs">All cases</span>
          </button>
        </div>
        <div className="rounded-xl border p-4 space-y-3">
          <div className="flex flex-wrap justify-between items-center gap-3">
            <div>
              <h3 className="font-bold">Research queue worker</h3>
              <p className="text-xs text-gray-600">Consumes queued unresolved canonical products. No internet calls until you start this worker.</p>
              {connection&&<p className="text-xs mt-1">DonSeTch: <strong>{connection.donsetch_installed?'Installed':'Not installed'}</strong> · Gemini key: <strong>{connection.gemini_configured?'Configured':'Missing'}</strong> · Model: {connection.model}</p>}
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs">Limit
                <input type="number" min={1} max={100} value={runLimit} onChange={e=>setRunLimit(Number(e.target.value))}
                  className="block w-20 rounded-lg border p-2 text-sm"/>
              </label>
              <label className="text-xs">Pages / product
                <select className="block w-24 rounded-lg border p-2 text-sm" value={maxPages}
                  onChange={e=>setMaxPages(Number(e.target.value))}>
                  {[1,2,3,4,5].map(x=><option value={x} key={x}>{x}</option>)}
                </select>
              </label>
              <label className="text-xs">High confidence
                <select className="block w-24 rounded-lg border p-2 text-sm" value={reviewThreshold}
                  onChange={e=>setReviewThreshold(Number(e.target.value))}>
                  {[.7,.8,.85,.9,.95].map(x=><option value={x} key={x}>{pct(x)}</option>)}
                </select>
              </label>
              <button type="button" disabled={busy||run?.status==='running'||!connection?.donsetch_installed||!connection?.gemini_configured}
                onClick={()=>operate(async()=>{
                  const started=await request('/run',{limit:runLimit,max_pages:maxPages,min_confidence:reviewThreshold});
                  setRun({id:started.run_id,status:'running',processed:0,queued_count:started.count});return started;
                })} className="rounded-lg bg-purple-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">Run research</button>
              {run?.status==='running'&&<button type="button" disabled={busy} className={button}
                onClick={()=>operate(()=>request('/runs/'+encodeURIComponent(run.id)+'/cancel',{}))}>Stop after current case</button>}
            </div>
          </div>
          {run&&<p className="text-xs">Run {run.id}: <strong>{run.status}</strong> · {run.processed||0}/{run.queued_count||0} processed · {run.succeeded||0} candidates · {run.failed||0} errors</p>}
          <p className="text-xs text-gray-500">Each case uses at most a bounded number of registered-source pages. Confidence is an uncalibrated ranking score, not a guarantee of correctness. No automatic INCI approval.</p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex-1 min-w-[180px] text-xs font-semibold">Search products
            <input className={input} value={search} onChange={e=>{setSearch(e.target.value);setPage(0)}} placeholder="brand, product, source URL"/>
          </label>
          <label className="text-xs font-semibold">Min confidence
            <select value={minConfidence} className={input} onChange={e=>{setMinConfidence(Number(e.target.value));setPage(0)}}>
              {[0,.5,.7,.85,.9].map(v=><option key={v} value={v}>{pct(v)}</option>)}
            </select>
          </label>
          <button className={button} type="button" disabled={busy} onClick={refresh}>Refresh</button>
        </div>
        <div className="overflow-x-auto rounded-xl border">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50"><tr>{['Product','Status','Confidence','Validation','Source','Attempts','Review'].map(c=><th key={c} className="p-3 text-left text-xs">{c}</th>)}</tr></thead>
            <tbody>
              {(data.items||[]).map(item=><tr key={item.canonical_product_id} className="border-t align-top">
                <td className="p-3 max-w-[250px]"><div className="font-semibold">{item.brand}</div><div>{item.canonical_title}</div><div className="text-xs text-gray-500">Canonical #{item.canonical_product_id}</div></td>
                <td className="p-3">{item.status.replaceAll('_',' ')}</td>
                <td className="p-3 font-semibold">{pct(item.confidence)}<div className="text-xs text-gray-500">Model {pct(item.model_confidence)}</div></td>
                <td className="p-3 text-xs">{item.completeness||'—'}<div>Identity {pct(item.identity_confidence)}</div><div>Source {pct(item.source_confidence)}</div></td>
                <td className="p-3 max-w-[190px]">{item.source_url?<a href={item.source_url} target="_blank" rel="noopener noreferrer" className="text-purple-700 underline break-all">{item.source_domain||'Source page'}</a>:'—'}</td>
                <td className="p-3">{item.attempts}</td>
                <td className="p-3">
                  <button type="button" className={button} onClick={()=>{setSelected(item);setReason('');setRegionApproved(false);setCorrection(false)}}>Inspect</button>
                </td>
              </tr>)}
              {!data.items?.length&&<tr><td colSpan={7} className="p-8 text-center text-sm text-gray-500">No cases match these filters.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between text-xs">
          <span>{data.total||0} matching · page {page+1}</span>
          <div className="flex gap-2">
            <button className={button} disabled={page===0} onClick={()=>setPage(p=>Math.max(0,p-1))}>Previous</button>
            <button className={button} disabled={(page+1)*30>=(data.total||0)} onClick={()=>setPage(p=>p+1)}>Next</button>
          </div>
        </div>
        {details&&<div className="rounded-xl border-2 border-purple-200 p-4 space-y-3">
          <div className="flex justify-between items-center"><h3 className="font-bold">Evidence review · #{details.canonical_product_id}</h3>
            <button type="button" className={button} onClick={()=>setSelected(null)}>Close details</button></div>
          <p className="text-sm"><strong>{details.brand} — {details.canonical_title}</strong> · {details.status} · confidence {pct(details.confidence)}</p>
          {details.source_url&&<a href={details.source_url} rel="noopener noreferrer" target="_blank" className="text-sm text-purple-700 underline break-all">{details.source_url}</a>}
          {details.source_metadata?.model_explanation&&<p className="text-sm text-gray-600">{details.source_metadata.model_explanation}</p>}
          {details.error_message&&<p className="text-sm text-red-700">{details.error_message}</p>}
          {details.ingredients_raw&&<div><h4 className="text-xs uppercase font-bold text-gray-500 mb-1">Extracted complete INCI candidate · {details.ingredients_tokens?.length||0} tokens</h4>
            <div className="rounded-lg bg-gray-50 border p-3 text-xs leading-relaxed whitespace-pre-wrap">{details.ingredients_raw}</div></div>}
          {details.evidence_excerpt&&<details className="text-sm"><summary className="cursor-pointer font-semibold">Source excerpt used as evidence</summary>
            <div className="border bg-gray-50 rounded-lg mt-2 p-3 text-xs whitespace-pre-wrap max-h-48 overflow-y-auto">{details.evidence_excerpt}</div>
          </details>}
          {details.status==='needs_review'&&<div className="space-y-2">
            <label className="block text-xs font-semibold">Review rationale (min 8 characters)
              <textarea className={input} rows={2} value={reason} onChange={e=>setReason(e.target.value)} placeholder="Confirmed same formulation and region against source evidence"/>
            </label>
            <label className="flex gap-2 items-start text-xs"><input type="checkbox" checked={regionApproved} onChange={e=>setRegionApproved(e.target.checked)}/>I explicitly verified compatibility with the intended Egyptian/MENA formulation if the source is from a different market.</label>
            <label className="flex gap-2 items-start text-xs"><input type="checkbox" checked={correction} onChange={e=>setCorrection(e.target.checked)}/>Explicit correction: replace a previously approved, conflicting formulation (higher permission required).</label>
            <div className="flex gap-2">
              <button className="rounded-lg bg-gray-900 text-white px-4 py-2 text-sm font-semibold disabled:opacity-50"
                disabled={busy||reason.trim().length<8} onClick={()=>operate(async()=>{const x=await request('/queue/'+details.canonical_product_id+'/review',
                  {action:'approve',reason,market_compatible:regionApproved,correction});setSelected(null);return x})}>Approve after source check</button>
              <button className={button} disabled={busy||reason.trim().length<8} onClick={()=>operate(async()=>{const x=await request('/queue/'+details.canonical_product_id+'/review',{action:'reject',reason});setSelected(null);return x})}>Reject</button>
            </div>
          </div>}
          {['rejected','error','no_result'].includes(details.status)&&<button className={button} disabled={busy}
            onClick={()=>operate(async()=>{const x=await request('/queue/'+details.canonical_product_id+'/retry',{});setSelected(null);return x})}>Retry research</button>}
          {details.reviewed_by&&<p className="text-xs text-gray-500">Reviewed by {details.reviewed_by}: {details.review_reason}</p>}
        </div>}
      </div>
    </div>
  </div>;
}
