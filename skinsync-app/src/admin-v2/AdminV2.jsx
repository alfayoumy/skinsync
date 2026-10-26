import React, { useEffect, useState } from 'react';
import { Home, Workflow, Activity, Cpu, ClipboardList, Database,
  Tag, ShieldAlert, PanelLeftClose, ArrowLeft, Menu, RefreshCw } from 'lucide-react';
import { request, formatNumber } from './api';
import WorkflowWizard from './WorkflowWizard';
import AiOps from './AiOps';
import JobCenter from './JobCenter';
import AdminObservatory from '../AdminObservatory';
import AggregationReviewPanel from '../AggregationReviewPanel';
import CatalogWorkflowsPanel from '../CatalogWorkflowsPanel';

const NAV = [
  {id:'home', name:'Operations Home', icon:Home},
  {id:'wizard', name:'Catalog Wizard', icon:Workflow},
  {id:'jobs', name:'Job Center', icon:Activity},
  {id:'ai', name:'AI & API Operations', icon:Cpu},
  {id:'reviews', name:'Review Workbench', icon:ClipboardList},
  {id:'offers', name:'Prices & Stock', icon:Tag},
  {id:'catalog', name:'Catalog Explorer', icon:Database},
  {id:'maintenance', name:'Maintenance & Recovery', icon:ShieldAlert},
];
function initialRoute() {
  const id = window.location.hash.replace(/^#admin-v2\//, '').split('/')[0];
  return NAV.some(item=>item.id===id) ? id : 'home';
}
function Stat({label,value,description}) {
  return <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
    <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</div>
    <div className="mt-2 text-3xl font-semibold text-slate-900">{formatNumber(value)}</div>
    {description && <p className="mt-2 text-xs text-slate-500">{description}</p>}
  </section>;
}
function OperationsHome({navigate}) {
  const [data,setData]=useState(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  async function refresh() {
    setBusy(true);
    try { setData(await request('/admin/v2/operations')); setError(''); }
    catch(e) { setError(e.message); }
    finally { setBusy(false); }
  }
  useEffect(()=>{ refresh(); const timer=setInterval(refresh,5000); return ()=>clearInterval(timer); },[]);
  const active = data?.active_pipeline_jobs || [];
  const workers = data?.inci_workers || {};
  const workerCount = ['jev','gemini'].filter(k=>workers[k]?.is_running).length +
    (data?.waterfall?.is_running ? 1 : 0);
  return <div className="space-y-6">
    <header className="flex items-center justify-between gap-3">
      <div><h2 className="text-2xl font-bold">Operations overview</h2>
        <p className="text-sm text-slate-500">Resume work, inspect live jobs, and follow the catalog build sequence.</p></div>
      <button className="rounded-lg border px-3 py-2 text-sm" onClick={refresh} disabled={busy}><RefreshCw size={16} className="inline mr-2"/>Refresh</button>
    </header>
    {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <div className="grid gap-4 md:grid-cols-3">
      <Stat label="Active pipeline jobs" value={active.length} description="From persisted pipeline task records"/>
      <Stat label="Active INCI workers" value={workerCount} description="Process-local status; restart may interrupt these jobs"/>
      <Stat label="Saved workflows" value={data?.workflows?.length ?? '—'} description="Most recently edited, up to eight"/>
    </div>
    <section className="rounded-2xl border bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4"><h3 className="font-semibold text-lg">Your workflows</h3>
        <button className="bg-violet-700 text-white rounded-lg px-4 py-2 text-sm font-medium" onClick={()=>navigate('wizard')}>Create or resume workflow</button></div>
      {!data?.workflows?.length && <p className="text-sm text-slate-500">No saved workflows. Create one to keep your step settings and execution history.</p>}
      <div className="divide-y">{(data?.workflows || []).map(w=><button key={w.id} onClick={()=>navigate('wizard')}
        className="text-left w-full py-3 hover:bg-slate-50 flex items-center justify-between gap-3">
        <span><strong className="block">{w.title}</strong><small className="text-slate-500">{w.id} · {w.current_step}</small></span>
        <span className="text-sm text-slate-600">{w.status}</span></button>)}</div>
    </section>
    <section className="rounded-2xl border bg-white p-5">
      <div className="flex justify-between items-center mb-3"><h3 className="font-semibold text-lg">Currently running</h3>
        <button className="text-violet-700 text-sm font-medium" onClick={()=>navigate('jobs')}>All jobs →</button></div>
      {!active.length && <p className="text-sm text-slate-500">No active persisted pipeline tasks. Check Job Center for INCI worker state.</p>}
      {active.map(t=><div key={t.task_id} className="flex justify-between border-t py-3 text-sm gap-3">
        <span><b>{t.task_type}</b><span className="block font-mono text-xs text-slate-500">{t.task_id}</span></span>
        <span className="text-right">{t.current_stage || t.status}<span className="block text-slate-500">{t.processed_items}/{t.total_items || '?'} processed</span></span>
      </div>)}
    </section>
    {data?.note && <p className="text-xs text-amber-800">{data.note}</p>}
  </div>;
}
export default function AdminV2({onBackToApp}) {
  const [route,setRoute]=useState(initialRoute);
  const [aiTab,setAiTab]=useState('providers');
  const [legacy,setLegacy]=useState(false);
  const [menuOpen,setMenuOpen]=useState(false);
  function navigate(to,options={}) {
    if(to==='ai' && options.tab)setAiTab(options.tab);
    if(to==='catalog')setLegacy(true);
    else setLegacy(false);
    setRoute(to); setMenuOpen(false);
    window.location.hash='admin-v2/'+to;
  }
  useEffect(()=>{
    const listen=()=>setRoute(initialRoute());
    window.addEventListener('hashchange',listen);
    return ()=>window.removeEventListener('hashchange',listen);
  },[]);
  const selected= NAV.find(n=>n.id===route)||NAV[0];
  return <div className="min-h-screen bg-slate-50 text-slate-900 flex">
    <aside className={`shrink-0 w-64 bg-slate-950 text-slate-100 min-h-screen z-20 ${menuOpen?'block':'hidden'} lg:block`}>
      <div className="p-6 border-b border-slate-800">
        <div className="font-extrabold text-xl tracking-tight">SkinSync <span className="text-violet-400">Admin</span></div>
        <div className="text-xs text-slate-400 mt-1">Operations Center · v2</div>
      </div>
      <nav className="p-3 space-y-1" aria-label="Admin sections">{NAV.map(n=><button key={n.id}
        onClick={()=>navigate(n.id)} aria-current={route===n.id?'page':undefined}
        className={`w-full rounded-xl px-4 py-3 text-sm flex gap-3 items-center text-left ${route===n.id?'bg-violet-600 text-white':'text-slate-300 hover:bg-slate-800'}`}>
        <n.icon size={18}/>{n.name}</button>)}</nav>
      <div className="p-4 border-t border-slate-800 mt-5">
        <button onClick={onBackToApp} className="text-sm text-slate-300 flex gap-2 items-center"><ArrowLeft size={16}/>Back to shop</button>
      </div>
    </aside>
    <div className="min-w-0 flex-1">
      <header className="bg-white border-b border-slate-200 px-5 py-4 flex justify-between gap-3 items-center sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <button className="lg:hidden" aria-label="Open menu" onClick={()=>setMenuOpen(!menuOpen)}><Menu/></button>
          <h1 className="font-semibold">{selected.name}</h1>
        </div>
        <button className="text-xs text-violet-700 font-semibold" onClick={()=>navigate('jobs')}>Inspect all jobs →</button>
      </header>
      <main className="p-4 md:p-7 max-w-[1500px] mx-auto">
        {route==='home' && <OperationsHome navigate={navigate}/>}
        {route==='wizard' && <WorkflowWizard navigate={navigate}/>}
        {route==='jobs' && <JobCenter/>}
        {route==='ai' && <AiOps tab={aiTab} onTabChange={setAiTab} navigate={navigate}/>}
        {route==='reviews' && <AggregationReviewPanel mode="reviews" onClose={()=>navigate('home')}/>}
        {route==='offers' && <WorkflowWizard navigate={navigate} workflowKind="offer_refresh"/>}
        {route==='catalog' && <div className="space-y-3">
          <div className="rounded-xl bg-white p-3 border text-sm">Existing catalog explorer is retained while the new workflow navigation is rolled out.</div>
          {legacy && <AdminObservatory onBackToApp={()=>navigate('home')}/>}</div>}
        {route==='maintenance' && <section className="space-y-4"><h2 className="text-xl font-semibold">Maintenance & recovery</h2>
          <p className="text-sm text-slate-600">Catalog resets, backups and restores remain in the existing guarded operations panel until all migration and lock tests pass.</p>
          <CatalogWorkflowsPanel initialTab="products" onClose={()=>navigate('home')}/></section>}
      </main>
    </div>
  </div>;
}
