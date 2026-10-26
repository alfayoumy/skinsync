import React,{useEffect,useState} from 'react';
import {RefreshCw,Pause,Square,Play,AlertTriangle,ScrollText} from 'lucide-react';
import {request,post,formatNumber} from './api';

const button='rounded-lg border px-3 py-2 text-sm font-medium disabled:opacity-40 hover:bg-slate-50';
function State({status}) {
  const color=status==='running'?'bg-blue-100 text-blue-700':
    status==='failed'||status==='error'?'bg-red-100 text-red-700':
    status==='paused'?'bg-amber-100 text-amber-700':'bg-slate-100 text-slate-700';
  return <span className={`rounded-full px-2 py-1 text-xs font-semibold ${color}`}>{status||'unknown'}</span>;
}
export default function JobCenter() {
  const [jobs,setJobs]=useState([]);
  const [selected,setSelected]=useState('');
  const [details,setDetails]=useState(null);
  const [type,setType]=useState('all');
  const [status,setStatus]=useState('all');
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  async function refresh() {
    try {
      const result=await request('/admin/v2/jobs?limit=150');
      setJobs(result.jobs||[]);
      setError('');
    }catch(e){setError(e.message);}
  }
  async function inspect(id) {
    setSelected(id);
    try {setDetails(await request('/admin/v2/jobs/'+encodeURIComponent(id)));setError('');}
    catch(e){setError(e.message);setDetails(null);}
  }
  useEffect(()=>{refresh();const interval=setInterval(refresh,3000);return()=>clearInterval(interval);},[]);
  useEffect(()=>{
    if(!selected)return;
    const interval=setInterval(()=>request('/admin/v2/jobs/'+encodeURIComponent(selected))
      .then(setDetails).catch(()=>{}),3000);
    return()=>clearInterval(interval);
  },[selected]);
  async function control(action) {
    if(!selected)return;
    if(action==='stop'&&!window.confirm('Request stop for '+selected+'? Already committed batches will not be rolled back.'))return;
    setBusy(true);setError('');setMessage('');
    try {
      const result=await post('/admin/v2/jobs/'+encodeURIComponent(selected)+'/control',{action});
      setMessage(result.warning||'Worker '+action+' command sent. Verify the acknowledged state below.');
      await Promise.all([inspect(selected),refresh()]);
    } catch(e){setError(e.message);}
    finally{setBusy(false);}
  }
  const visible=jobs.filter(j=>(type==='all'||j.task_type===type)&&
    (status==='all'||j.status===status));
  const types=[...new Set(jobs.map(j=>j.task_type).filter(Boolean))];
  const states=[...new Set(jobs.map(j=>j.status).filter(Boolean))];
  const job=details?.job;
  const current=jobs.find(j=>j.id===selected);
  const available=current?.controls||[];
  const pct=job?.total_items>0?Math.min(100,Math.round(100*(job.processed_items||0)/job.total_items)):null;
  return <div className="space-y-5">
    <header className="flex justify-between gap-3 items-center flex-wrap">
      <div><h2 className="text-2xl font-bold">Job Center</h2><p className="text-sm text-slate-500">Discover jobs started in another browser session, see checkpoints, and inspect failures.</p></div>
      <button className={button} onClick={refresh}><RefreshCw size={15} className="inline mr-2"/>Refresh</button>
    </header>
    {error && <p role="alert" className="rounded-xl bg-red-50 border border-red-200 p-3 text-red-700 text-sm">{error}</p>}
    {message && <p role="status" className="rounded-xl bg-amber-50 border border-amber-200 p-3 text-amber-800 text-sm">{message}</p>}
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]">
      <section className="rounded-2xl border bg-white p-4 min-w-0">
        <div className="flex flex-wrap gap-3 mb-4">
          <select aria-label="Filter job type" className="rounded-lg border p-2 text-sm min-w-0 flex-1" value={type} onChange={e=>setType(e.target.value)}>
            <option value="all">All job types</option>{types.map(t=><option key={t} value={t}>{t}</option>)}
          </select>
          <select aria-label="Filter status" className="rounded-lg border p-2 text-sm" value={status} onChange={e=>setStatus(e.target.value)}>
            <option value="all">All statuses</option>{states.map(s=><option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="overflow-auto"><table className="w-full text-left text-sm">
          <thead className="bg-slate-50"><tr><th className="px-3 py-3">Job</th><th className="px-3">Status</th><th className="px-3">Progress</th></tr></thead>
          <tbody>{visible.map(j=><tr key={j.id} onClick={()=>inspect(j.id)}
            className={`cursor-pointer border-t hover:bg-violet-50 ${selected===j.id?'bg-violet-50':''}`}>
            <td className="px-3 py-3"><b className="block">{j.task_type}</b>
              <small className="font-mono text-slate-500 break-all">{j.id}</small>
              <small className="text-slate-500 block">{j.current_stage||j.started_at}</small></td>
            <td className="px-3"><State status={j.status}/></td>
            <td className="px-3 whitespace-nowrap">{formatNumber(j.processed_items)} / {j.total_items||'?'}</td>
          </tr>)}</tbody>
        </table>{!visible.length&&<p className="text-sm text-slate-500 p-3">No jobs match this filter.</p>}</div>
      </section>
      <section className="rounded-2xl border bg-white p-5 min-w-0">
        {!job?<div className="text-slate-500 text-sm">Select a job to see counters, available controls, execution checkpoints, and logs.</div>:<>
          <div className="flex justify-between gap-3 flex-wrap"><h3 className="text-lg font-bold">{job.task_type||job.name||current?.task_type||'Job details'}</h3>
            <State status={job.status}/></div>
          <p className="font-mono text-xs text-slate-500 mt-2 break-all">{selected}</p>
          {details.kind==='process_local'&&<div className="mt-3 flex gap-2 rounded-lg bg-amber-50 border border-amber-200 p-3 text-xs text-amber-900">
            <AlertTriangle size={18} className="shrink-0"/>This legacy worker keeps its progress in process memory. Restart recovery is not guaranteed.</div>}
          <div className="grid grid-cols-3 gap-2 mt-5">
            {[['Processed',job.processed_items],['Succeeded',job.successful_items??job.stats?.approved],['Failed',job.failed_items??job.stats?.rejected]].map(([k,v])=>
              <div key={k} className="rounded-lg bg-slate-50 p-3"><div className="text-xs text-slate-500">{k}</div><div className="font-bold text-lg">{formatNumber(v)}</div></div>)}
          </div>
          <p className="mt-4 text-sm"><b>Current stage:</b> {job.current_stage||job.current_product||'—'}</p>
          <div className="flex justify-between mt-4 text-xs text-slate-500"><span>{formatNumber(job.processed_items)} completed</span><span>{pct===null?'Unknown total':pct+'%'}</span></div>
          <div className="h-2 rounded-full bg-slate-100 mt-2 overflow-hidden">
            {pct!==null&&<div className="h-2 bg-violet-600" style={{width:pct+'%'}}/>}</div>
          <div className="mt-4 flex gap-2 flex-wrap">
            <button className={button} disabled={busy||!available.includes('pause')} onClick={()=>control('pause')}><Pause size={14} className="inline mr-1"/>Pause</button>
            <button className={button} disabled={busy||!available.includes('resume')} onClick={()=>control('resume')}><Play size={14} className="inline mr-1"/>Resume</button>
            <button className={button} disabled={busy||!available.includes('stop')} onClick={()=>control('stop')}><Square size={14} className="inline mr-1"/>Stop</button>
          </div>
          {!available.length&&<p className="text-xs text-slate-500 mt-2">No safe lifecycle controls are exposed for this job state.</p>}
          <h4 className="mt-6 font-semibold">Progress and checkpoint metadata</h4>
          <pre className="text-xs bg-slate-950 text-slate-100 p-3 rounded-xl overflow-auto max-h-52 mt-2 whitespace-pre-wrap">{JSON.stringify(job.metadata||job.stats||{},null,2)}</pre>
          <h4 className="mt-5 font-semibold"><ScrollText size={17} className="inline mr-2"/>Recent events</h4>
          <div className="mt-2 border rounded-xl max-h-72 overflow-y-auto divide-y">
            {(job.logs||job.recent_logs||[]).length===0&&<p className="text-sm text-slate-500 p-3">No stored logs for this job.</p>}
            {(job.logs||job.recent_logs||[]).map((log,i)=><div key={log.id||i} className="p-3 text-xs">
              <span className="text-slate-400">{log.created_at||''}</span> {log.message||log}</div>)}
          </div>
          {job.error_message&&<p role="alert" className="mt-3 text-sm text-red-700">{job.error_message}</p>}
        </>}
      </section>
    </div>
  </div>;
}
