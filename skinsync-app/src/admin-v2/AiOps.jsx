import React, { useEffect, useState } from 'react';
import { Activity, RefreshCw, Play, Pause, Square, RotateCcw,
  AlertCircle, ShieldCheck, Settings2, Layers, Clock, Search } from 'lucide-react';
import { request, post, formatNumber } from './api';

const TABS=[
  ['providers','Providers & Health'],
  ['queues','Queues'],
  ['batches','Batch Manager'],
  ['settings','Settings & Limits'],
];
const PANEL='rounded-2xl border border-slate-200 bg-white p-5 shadow-sm';
const BTN='rounded-lg px-3 py-2 text-sm font-medium border border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40';
const PRIMARY='rounded-lg px-4 py-2 text-sm bg-violet-700 hover:bg-violet-800 font-semibold text-white disabled:opacity-40';
const INPUT='w-full rounded-lg border border-slate-300 bg-white p-2 text-sm';
const number = v => formatNumber(v);
function Message({error,success}) {
  return <>{error && <div role="alert" className="p-3 bg-red-50 border border-red-200 text-red-800 rounded-xl text-sm">{error}</div>}
    {success && <div role="status" className="p-3 bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-xl text-sm">{success}</div>}</>;
}
function WorkerControls({worker, state, onAction, busy}) {
  const running=!!state?.is_running, paused=!!state?.is_paused;
  return <div className="flex gap-2 flex-wrap">
    <button className={BTN} disabled={busy||!running||paused} onClick={()=>onAction(worker,'pause')}><Pause size={14} className="inline mr-1"/>Pause</button>
    <button className={BTN} disabled={busy||!paused} onClick={()=>onAction(worker,'resume')}><Play size={14} className="inline mr-1"/>Resume</button>
    <button className={BTN} disabled={busy||!running} onClick={()=>onAction(worker,'stop')}><Square size={14} className="inline mr-1"/>Stop</button>
  </div>;
}
function Progress({item}) {
  const total=Number(item?.total_items)||0;
  const done=Number(item?.processed_items)||0;
  const pct=total?Math.min(100,Math.round(done*100/total)):null;
  return <div><div className="flex justify-between text-xs text-slate-500 mb-2">
    <span>{number(done)} processed / {total?number(total):'unknown target'}</span><span>{pct===null?'Unbounded':pct+'%'}</span></div>
    <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
      {pct!==null && <div className="h-2 bg-violet-600" style={{width:pct+'%'}}/>}
    </div></div>;
}
export default function AiOps({tab='providers',onTabChange,navigate}) {
  const [apis,setApis]=useState(null);
  const [queues,setQueues]=useState(null);
  const [identity,setIdentity]=useState(null);
  const [waterfall,setWaterfall]=useState(null);
  const [research,setResearch]=useState(null);
  const [researchConnection,setResearchConnection]=useState(null);
  const [batchPresets,setBatchPresets]=useState({presets:[]});
  const [presetId,setPresetId]=useState('');
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  const [batchKind,setBatchKind]=useState('identity');
  const [limit,setLimit]=useState(100);
  const [batchSize,setBatchSize]=useState(10);
  const [preview,setPreview]=useState(null);
  const [savedPreview,setSavedPreview]=useState(null);
  async function refresh() {
    const paths=['/admin/apis/status','/admin/queues/status','/admin/aggregation/reviews/gemini/queue','/admin/waterfall/batch/status','/admin/inci-research/queue?limit=1','/admin/inci-research/connection','/admin/v2/batch-presets'];
    const results=await Promise.allSettled(paths.map(p=>request(p)));
    const setters=[setApis,setQueues,setIdentity,setWaterfall,setResearch,setResearchConnection,setBatchPresets];
    results.forEach((r,i)=>{if(r.status==='fulfilled')setters[i](r.value);});
    const failed=results.map((r,i)=>r.status==='rejected'?paths[i]+': '+r.reason.message:null).filter(Boolean);
    setError(failed.join(' | '));
  }
  useEffect(()=>{refresh();const id=setInterval(refresh,5000);return ()=>clearInterval(id);},[]);
  async function perform(fn,done) {
    setBusy(true);setError('');setMessage('');
    try {const result=await fn();setMessage(done||result?.message||'Action accepted. Refresh to see its current state.');await refresh();return result;}
    catch(e){setError(e.message);return null;}finally{setBusy(false);}
  }
  async function control(worker,action) {
    await perform(()=>post('/admin/queues/'+action,{worker}),action+' requested for '+worker+'.');
  }
  const providers=[apis?.gemini,apis?.jev,apis?.langsearch].filter(Boolean);
  const kindLabels={identity:'Gemini — product identity',jev:'Jev — INCI queue',gemini:'Gemini — INCI queue',
    saved:'Apply saved identity verdicts (no API)',waterfall:'INCI waterfall / LangSearch research'};
  const queueValue=()=>{
    if(batchKind==='identity') return identity?.counts?.queued ?? identity?.counts?.waiting ?? 0;
    if(batchKind==='jev')return queues?.jev?.queue_depth?.pending ?? 0;
    if(batchKind==='gemini')return queues?.gemini?.queue_depth?.pending ?? 0;
    if(batchKind==='waterfall')return 'Verify eligibility in wizard step 5';
    return 'Saved verdicts (re-evaluated locally)';
  };
  async function previewBatch() {
    if(batchKind==='saved') {
      const p=await perform(()=>post('/admin/aggregation/reviews/gemini/saved/preview',
        {limit:Number(limit),confidence_threshold:0.90}),'Saved decisions checked locally. Zero provider calls.');
      if(p)setSavedPreview(p);
    } else {
      setPreview({kind:batchKind, limit:Number(limit),batchSize:Number(batchSize),eligible:queueValue(),
        note:batchKind==='identity'?'Each request compares exactly two products; the identity endpoint accepts batch_size=1.':
          batchKind==='waterfall'?'LangSearch is part of the INCI waterfall, not a separate model approval queue.':
          'This is an eligibility/queue-depth preview. Actual API usage depends on processing and rate limits.'});
      setMessage('Preview calculated from current queue statistics. Recheck before launch.');
    }
  }
  async function startBatch() {
    if(!window.confirm('Run '+kindLabels[batchKind]+' on up to '+limit+' items? This may create provider API requests and save results.'))return;
    const body={limit:Number(limit)};
    let path;let payload=body;
    if(batchKind==='identity'){path='/admin/aggregation/reviews/gemini/run';payload={...body,batch_size:1};}
    if(batchKind==='jev'){path='/admin/queues/start';payload={worker:'jev',...body};}
    if(batchKind==='gemini'){path='/admin/queues/start';payload={worker:'gemini',...body,batch_size:Number(batchSize),all_pending:false};}
    if(batchKind==='saved'){path='/admin/aggregation/reviews/gemini/saved/apply';payload={...body,confidence_threshold:0.90};}
    if(batchKind==='waterfall'){path='/admin/waterfall/batch/start';payload={scope:'unmatched_only',limit:Number(limit),langsearch_enabled:true,queue_jev:false,queue_gemini:false,queue_inci_research:false,max_workers:2,dry_run:true};}
    const r=await perform(()=>post(path,payload),'Execution started. Track its task in Job Center.');
    if(r){setPreview(null);setSavedPreview(null);}
  }
  async function savePreset() {
    const name=window.prompt('Name this batch preset (saved server-side; no job will start):');
    if(!name?.trim())return;
    await perform(()=>post('/admin/v2/batch-presets',{
      name:name.trim(),operation:batchKind,limit:Number(limit),batch_size:Number(batchSize)
    }),'Batch preset saved.');
  }
  async function deletePreset() {
    if(!presetId)return;
    if(!window.confirm('Delete this saved batch preset? Active jobs are unaffected.'))return;
    await perform(()=>request('/admin/v2/batch-presets/'+encodeURIComponent(presetId),{method:'DELETE'}),'Batch preset deleted.');
    setPresetId('');
  }
  function loadPreset(id) {
    setPresetId(id);
    const entry=(batchPresets.presets||[]).find(p=>p.id===id);
    if(entry){
      setBatchKind(entry.operation);setLimit(entry.limit_items);setBatchSize(entry.batch_size);
      setPreview(null);setSavedPreview(null);
    }
  }
  async function quotaAction(name) {
    const text=name==='reset'?'RESET LOCAL LANGSEARCH COUNTER':'MARK LANGSEARCH EXHAUSTED';
    if(window.prompt('Local quota bookkeeping only; this does NOT change the external provider allowance. Type '+text+' to confirm.')!==text)return;
    await perform(()=>post('/admin/langsearch/'+(name==='reset'?'reset-quota':'set-quota-exhausted'),
      name==='reset'?{}:{reason:'Operator marked daily limit exhausted in Admin V2'}),
      'Local LangSearch quota state updated. Provider-side limits are unchanged.');
  }
  return <div className="space-y-5">
    <div><h2 className="text-2xl font-bold">AI & API Operations</h2><p className="text-slate-500 text-sm">Live provider telemetry, independent queues, batch execution and effective runtime limits.</p></div>
    <nav className="flex flex-wrap gap-2" aria-label="AI operations tabs">{TABS.map(([id,label])=>
      <button key={id} className={tab===id?PRIMARY:BTN} aria-current={tab===id?'page':undefined} onClick={()=>onTabChange(id)}>{label}</button>)}</nav>
    <div className="flex justify-end"><button className={BTN} onClick={refresh} disabled={busy}><RefreshCw size={15} className="inline mr-2"/>Refresh live status</button></div>
    <Message error={error} success={message}/>
    {tab==='providers' && <div className="space-y-4">
      {apis?.summary?.issues?.length>0 && <section className="border border-amber-200 bg-amber-50 rounded-xl p-4 text-sm">
        <strong>Provider alerts</strong>{apis.summary.issues.map((s,i)=><p key={i} className="mt-2">{s}</p>)}</section>}
      <div className="grid xl:grid-cols-3 gap-4">{providers.map(p=><section key={p.name} className={PANEL}>
        <div className="flex justify-between gap-2"><h3 className="font-bold">{p.name}</h3>
          <span className={'rounded-full px-2 py-1 text-xs '+(p.is_configured?'bg-slate-100 text-slate-700':'bg-amber-100 text-amber-800')}>{p.status}</span></div>
        <p className="text-sm text-slate-500 mt-2">{p.role}</p>
        <div className="mt-4 space-y-2 text-sm"><p><b>Model:</b> {p.model||'Web search provider'}</p>
          <p><b>Configured:</b> {p.is_configured?'Yes':'No'}</p>
          {p.tokens_used_today!==undefined && <p><b>Tokens used today:</b> {number(p.tokens_used_today)} / {number(p.daily_token_limit)}</p>}
          {p.resets_in_human && <p><b>Next reset:</b> {p.resets_in_human}</p>}
          <div className="pt-2 border-t"><b>Effective limits</b>{Object.entries(p.limits||{}).filter(([,v])=>!Array.isArray(v)).map(([key,value])=>
            <p key={key} className="mt-2 flex justify-between gap-2"><span className="text-slate-500">{key.replaceAll('_',' ')}</span><span>{String(value)}</span></p>)}</div>
        </div></section>)}</div>
      <section className={PANEL}><h3 className="font-bold">DonSeTch MCP research adapter</h3>
        <p className="text-sm mt-2">Installed: <b>{researchConnection?.donsetch_installed?'Yes':'No / unknown'}</b> · Gemini configured: <b>{researchConnection?.gemini_configured?'Yes':'No'}</b> · Model: {researchConnection?.model||'—'}</p>
        <p className="text-xs text-slate-500 mt-2">Research generates candidate-only evidence. Formulation publication still requires independent proof and review.</p></section>
      <p className="text-xs text-slate-500">Health is based on existing configured state and recorded usage; it is not a live remote-provider uptime test. Provider quotas may differ from local counters.</p>
    </div>}
    {tab==='queues' && <div className="grid gap-4 xl:grid-cols-2">
      {['jev','gemini'].map(worker=><section className={PANEL} key={worker}>
        <div className="flex justify-between"><h3 className="text-lg font-bold">{worker==='jev'?'Jev INCI verification':'Gemini INCI verification'}</h3>
          <span className="text-sm text-slate-500">{queues?.[worker]?.status||'Unknown'}</span></div>
        <p className="text-sm text-slate-500 my-3">Pending: {number(queues?.[worker]?.queue_depth?.pending)} · Approved: {number(queues?.[worker]?.queue_depth?.approved)} · Rejected: {number(queues?.[worker]?.queue_depth?.rejected)}</p>
        <Progress item={queues?.[worker]}/>
        <p className="text-xs my-3 text-slate-500">{queues?.[worker]?.current_product}</p>
        <WorkerControls worker={worker} state={queues?.[worker]} busy={busy} onAction={control}/>
        <button className="mt-3 text-violet-700 text-sm font-medium" onClick={()=>onTabChange('batches')}>Configure new batch →</button>
      </section>)}
      <section className={PANEL}><h3 className="font-bold text-lg">Gemini product identity</h3>
        <p className="mt-2 text-sm text-slate-500">Separate review queue and deterministic variant/identity guards. Not an INCI approval queue.</p>
        <div className="mt-3 text-sm">Unresolved identity reviews: <b>{number(identity?.pending_identity_cases)}</b></div>
        <div className="flex flex-wrap gap-3 text-xs mt-3">{Object.entries(identity?.counts||{}).map(([k,v])=><span key={k} className="rounded-md bg-slate-100 p-2">{k}: {number(v)}</span>)}</div>
        <div className="mt-4 flex gap-2 flex-wrap">
          <button className={BTN} disabled={busy} onClick={()=>perform(()=>post('/admin/aggregation/reviews/gemini/enqueue',{limit:200}),'Eligible identity cases enqueued.')}>Enqueue eligible</button>
          <button className={PRIMARY} onClick={()=>{setBatchKind('identity');onTabChange('batches');}}>Manage batch</button>
        </div>
      </section>
      <section className={PANEL}><h3 className="font-bold text-lg">INCI research & human review</h3>
        <p className="text-sm text-slate-600 my-3">LangSearch operates inside the INCI waterfall. It is not a separately stoppable approval queue. Human identity and formulation reviews are managed in Review Workbench.</p>
        <p className="text-sm">Waterfall: <b>{waterfall?.status||'unknown'}</b> · {number(waterfall?.processed_items)} / {number(waterfall?.total_items)} processed</p>
        <div className="flex gap-2 flex-wrap text-xs mt-3">{Object.entries(research?.counts||{}).map(([k,v])=>
          <span key={k} className="rounded-md bg-slate-100 p-2">{k}: {number(v)}</span>)}</div>
        <p className="text-xs text-slate-500 mt-2">DonSeTch MCP installed: {researchConnection?.donsetch_installed?'Yes':'No / unknown'} · Model: {researchConnection?.model||'—'}</p>
        <div className="mt-4 flex gap-2"><button className={BTN} onClick={()=>navigate('wizard')}>Go to INCI step</button>
          <button className={BTN} onClick={()=>navigate('reviews')}>Review cases</button></div>
      </section>
    </div>}
    {tab==='batches' && <section className={PANEL}>
      <div className="flex justify-between flex-wrap gap-2"><div><h3 className="text-lg font-bold">Start a controlled batch</h3>
        <p className="text-sm text-slate-500">Select a queue, inspect eligibility, then explicitly authorize execution.</p></div>
        <button className={BTN} onClick={()=>navigate('jobs')}>Job history →</button></div>
      <div className="mt-5 p-3 bg-slate-50 rounded-xl flex flex-wrap gap-3 items-end">
        <label className="text-sm min-w-[220px] flex-1">Saved batch presets
          <select className={INPUT+' mt-1'} value={presetId} onChange={e=>loadPreset(e.target.value)}>
            <option value="">Select saved preset</option>{(batchPresets.presets||[]).map(p=>
              <option key={p.id} value={p.id}>{p.name} · {p.operation} · {p.limit_items} items</option>)}
          </select>
        </label>
        <button className={BTN} disabled={busy} onClick={savePreset}>Save as preset</button>
        <button className={BTN} disabled={busy||!presetId} onClick={deletePreset}>Delete preset</button>
      </div>
      <div className="grid md:grid-cols-3 gap-4 mt-5">
        <label className="text-sm font-medium">Operation<select className={INPUT+' mt-1'} value={batchKind} onChange={e=>{setBatchKind(e.target.value);setPreview(null);setSavedPreview(null);}}>
          {Object.entries(kindLabels).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label>
        <label className="text-sm font-medium">Maximum items<input className={INPUT+' mt-1'} type="number" min="1" max="20000" value={limit} onChange={e=>setLimit(Math.max(1,Math.min(20000,Number(e.target.value)||1)))}/></label>
        <label className="text-sm font-medium">Gemini INCI items per batch<input className={INPUT+' mt-1'} type="number" min="1" max="20" value={batchSize} disabled={batchKind!=='gemini'} onChange={e=>setBatchSize(Math.max(1,Math.min(20,Number(e.target.value)||1)))}/></label>
      </div>
      <div className="mt-5 rounded-xl bg-slate-50 p-4 text-sm">
        <p><b>Current eligible queue depth:</b> {number(queueValue())}</p>
        {batchKind==='identity' && <p className="mt-2">The identity Gemini endpoint compares two products per request and enforces its own hard gates.</p>}
        {batchKind==='waterfall' && <p className="mt-2">This creates a <b>dry-run</b> INCI waterfall with LangSearch enabled. Apply mode remains in the catalog wizard.</p>}
        {batchKind==='saved' && <p className="mt-2">Saved verdict replay does not call Gemini. Any stale or unsafe result remains unresolved.</p>}
      </div>
      {(preview||savedPreview) && <div className="mt-4 border rounded-xl p-4">
        <h4 className="font-semibold">Batch preview</h4>
        <p className="text-sm mt-2">{preview?.note||'Saved verdicts recalculated against current evidence.'}</p>
        <pre className="max-h-44 overflow-auto text-xs mt-3 whitespace-pre-wrap">{JSON.stringify(savedPreview||preview,null,2)}</pre>
      </div>}
      <div className="mt-5 flex gap-3 flex-wrap">
        <button className={BTN} onClick={previewBatch} disabled={busy}><Search size={15} className="inline mr-2"/>Preview</button>
        <button className={PRIMARY} onClick={startBatch} disabled={busy||!(preview||savedPreview)}><Play size={15} className="inline mr-2"/>Start batch</button>
      </div>
      <p className="mt-3 text-xs text-amber-800">Preview is advisory and not a reservation of provider quota. The backend performs the authoritative safety checks when starting the job.</p>
    </section>}
    {tab==='settings' && <div className="space-y-4">
      <section className={PANEL}><h3 className="font-bold text-lg">Effective provider configuration</h3>
        <p className="text-sm text-slate-500 my-3">Read-only: loaded from the backend runtime configuration. Changing model names, secrets or external provider caps requires deployment configuration; this screen will not pretend to save them.</p>
        <div className="grid lg:grid-cols-3 gap-4">{providers.map(p=><div className="rounded-xl bg-slate-50 p-4" key={p.name}>
          <h4 className="font-semibold">{p.name}</h4><p className="text-sm mt-2">Model: {p.model||'N/A'}</p>
          <p className="text-sm">Credentials: {p.is_configured?'configured':'missing'}</p>
          {Object.entries(p.limits||{}).filter(([,v])=>!Array.isArray(v)).map(([k,v])=><p key={k} className="text-xs mt-2 flex justify-between gap-2"><span>{k.replaceAll('_',' ')}</span><span>{String(v)}</span></p>)}
        </div>)}</div>
      </section>
      <section className={PANEL}><h3 className="font-bold text-lg">LangSearch local quota bookkeeping</h3>
        <p className="text-sm my-3">Resetting this counter does <b>not</b> reset or increase external LangSearch allowance. Use only after independently checking provider usage.</p>
        <div className="flex gap-2 flex-wrap">
          <button className={BTN} disabled={busy} onClick={()=>quotaAction('exhausted')}>Mark daily quota exhausted</button>
          <button className={BTN} disabled={busy} onClick={()=>quotaAction('reset')}>Reset local counter</button>
        </div>
      </section>
    </div>}
  </div>;
}
