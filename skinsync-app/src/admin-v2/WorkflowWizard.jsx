import React,{useEffect,useState} from 'react';
import {Check,ChevronRight,ChevronLeft,Play,Save,Search,AlertTriangle,RefreshCw,ExternalLink} from 'lucide-react';
import {request,post,put,formatNumber} from './api';

const STEPS=[
  ['sources','Sources & scope','Choose the source population and scan boundaries'],
  ['discovery','Discover & stage','Collect source evidence with live per-source progress'],
  ['identity','Identity resolution','Safely rematch and review ambiguous identities'],
  ['classification','Classify & validate','Inspect taxonomy and unverified identities'],
  ['inci','Retrieve full INCI','Run the evidence waterfall for verified skincare products'],
  ['inci_review','INCI review queues','Separate Jev, Gemini and human formulation reviews'],
  ['quality','Quality & finalize','Reconcile coverage and review the exceptions'],
];
const OFFER_STEPS=[STEPS[0],['discovery','Refresh existing offers','Update price and stock without creating products'],STEPS[6]];
const PANEL='rounded-2xl border border-slate-200 bg-white p-5 shadow-sm';
const BTN='rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-40';
const PRIMARY='rounded-lg bg-violet-700 hover:bg-violet-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40';
const INPUT='w-full rounded-lg border border-slate-300 bg-white p-2 text-sm';
const defaults={source_ids:[],start_page:1,pages:0,limit:200,scope:'unmatched_only',
  dry_run:true,langsearch_enabled:true,queue_jev:false,queue_gemini:false,
  queue_inci_research:false,max_workers:3,update_price:true,update_stock:true};
export default function WorkflowWizard({navigate,workflowKind='catalog_build'}) {
  const [runs,setRuns]=useState([]);
  const [run,setRun]=useState(null);
  const [step,setStep]=useState('sources');
  const [config,setConfig]=useState(defaults);
  const [sources,setSources]=useState([]);
  const [preview,setPreview]=useState(null);
  const [previewType,setPreviewType]=useState('');
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [runningJob,setRunningJob]=useState('');
  const [reviewCount,setReviewCount]=useState(null);
  const allSteps=workflowKind==='offer_refresh'?OFFER_STEPS:STEPS;
  async function loadRuns() {
    try{
      const data=await request('/admin/v2/workflows');
      setRuns((data.workflows||[]).filter(x=>x.kind===workflowKind));
    }catch(e){setError(e.message);}
  }
  async function loadRun(id) {
    try{
      const data=await request('/admin/v2/workflows/'+encodeURIComponent(id));
      setRun(data);
      const valid=(workflowKind==='offer_refresh'?OFFER_STEPS:STEPS).some(s=>s[0]===data.current_step);
      setStep(valid?data.current_step:'sources');
      setError('');
    }catch(e){setError(e.message);}
  }
  useEffect(()=>{loadRuns();request('/admin/aggregation/sources')
    .then(data=>setSources(data.sources||[])).catch(e=>setError(e.message));},[workflowKind]);
  useEffect(()=>{
    if(!run)return;
    const stored=run.steps?.find(s=>s.step_key===step);
    const sourceConfig=run.steps?.find(s=>s.step_key==='sources')?.configuration||{};
    setConfig({...defaults,...sourceConfig,...(stored?.configuration||{})});
    setPreview(stored?.status==='previewed'?stored.result:null);
    setPreviewType('');
    setRunningJob(stored?.job_id||'');
    setMessage('');setError('');
  },[run?.id,step]);
  async function createRun() {
    setBusy(true);
    try{
      const data=await post('/admin/v2/workflows',{kind:workflowKind,title:workflowKind==='offer_refresh'?'Price & stock refresh':'Catalog build'});
      setRun(data);setStep('sources');await loadRuns();
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function save(state='draft',result={},jobId=null) {
    if(!run)throw new Error('Create or select a workflow first.');
    const data=await put('/admin/v2/workflows/'+encodeURIComponent(run.id)+'/steps/'+step,{
      configuration:config,status:state,result,job_id:jobId,
    });
    setRun(data);
    if(jobId)setRunningJob(jobId);
    return data;
  }
  async function act(fn,onDone) {
    setBusy(true);setError('');setMessage('');
    try{const output=await fn();await onDone?.(output);return output;}
    catch(e){setError(e.message);return null;}finally{setBusy(false);}
  }
  async function saveDraft(){
    await act(()=>save('draft',preview||{}),()=>setMessage('Step configuration saved to the database.'));
  }
  function selectStep(target){
    if(busy)return;
    setStep(target);
  }
  async function previewAction() {
    await act(async()=>{
      let result,kind;
      if(step==='sources'){result={selected:config.source_ids,start_page:config.start_page,pages:config.pages};kind='scope';}
      else if(step==='discovery' && workflowKind==='offer_refresh'){
        if(config.source_ids.length!==1)throw Error('Select exactly one Shopify retailer for offer refresh.');
        result=await post('/admin/workflows/offers/refresh',{
          source_id:config.source_ids[0],start_page:config.start_page,pages:Math.max(1,config.pages||1),
          update_price:config.update_price,update_stock:config.update_stock,dry_run:true
        });kind='offers';
      }
      else if(step==='discovery'){
        if(!config.source_ids.length)throw Error('Choose at least one enabled source first.');
        // The preview is a real dry-run network scan, limited to one page per source.
        result=await post('/admin/workflows/products/discover-multiple',{
          source_ids:config.source_ids,start_page:config.start_page,pages:1,dry_run:true,urls_by_source:{}
        });kind='discovery';
      }
      else if(step==='identity'){
        result=await request('/admin/aggregation/reviews/rematch/preview?limit='+Number(config.limit));kind='rematch';
      }
      else if(step==='classification'){
        result=await request('/admin/aggregation/reviews?view=all&limit=10');
        kind='classification';
      }
      else if(step==='inci'){
        result=await request('/admin/waterfall/batch/eligibility?scope='+encodeURIComponent(config.scope));
        kind='eligibility';
      }
      else if(step==='inci_review'){
        result=await request('/admin/queues/status');
        kind='queues';
      }
      else {
        const [identity,inci]=await Promise.all([
          request('/admin/aggregation/reviews/gemini/queue'),
          request('/admin/waterfall/batch/eligibility?scope=unmatched_only')
        ]);
        result={identity_queue:identity,inci_eligibility:inci};
        kind='quality';
      }
      setPreview(result);setPreviewType(kind);
      await save('previewed',result,result?.task_id||null);
      setMessage(result?.task_id?'Dry-run job dispatched. Open Job Center to inspect completion.':'Read-only preview recorded. No catalog records changed.');
      return result;
    });
  }
  async function applyAction(){
    await act(async()=>{
      let result=null;
      if(step==='sources'){
        if(!config.source_ids.length)throw Error('Select at least one source.');
        await save('completed',{selected_sources:config.source_ids,scope:config});
        setMessage('Source selection saved. Continue to discovery.');
        return;
      }
      if(step==='discovery' && workflowKind==='offer_refresh'){
        if(config.source_ids.length!==1)throw Error('Select exactly one retailer.');
        if(!window.confirm('Update existing prices/stock across '+(config.pages||1)+' source pages? The current API operation is synchronous and cannot be paused.'))return;
        result=await post('/admin/workflows/offers/refresh',{
          source_id:config.source_ids[0],start_page:config.start_page,pages:Math.max(1,config.pages||1),
          update_price:config.update_price,update_stock:config.update_stock,dry_run:false
        });
        await save('completed',result);
        setMessage('Offer refresh finished. Review affected offers before another batch.');
        return result;
      }
      if(step==='discovery'){
        if(!config.source_ids.length)throw Error('Select at least one source.');
        if(!window.confirm('Start real discovery of '+config.source_ids.length+' source(s)? Saved observations and provisional products may be committed.'))return;
        result=await post('/admin/workflows/products/discover-multiple',{
          source_ids:config.source_ids,start_page:config.start_page,pages:Number(config.pages),dry_run:false,urls_by_source:{}
        });
        await save('running',result,result.task_id);setMessage('Real discovery started. Track progress in Job Center.');
        return result;
      }
      if(step==='identity'){
        if(!preview?.fingerprint||!preview?.can_start)throw Error('Run a fresh rematch preview and resolve blockers.');
        if(!window.confirm('Rematch up to '+config.limit+' saved observations? A consistent database backup will be created.'))return;
        result=await post('/admin/aggregation/reviews/rematch/start',{
          fingerprint:preview.fingerprint,confirmation:'REMATCH SAVED PRODUCTS',limit:Number(config.limit)
        });
        await save('running',result,result.task_id);setMessage('Rematch job started. Open Job Center for details.');
        return result;
      }
      if(step==='inci'){
        if(!preview||!(preview.eligible_before_limit>0))throw Error('No eligible verified canonical skincare products. Inspect identity blockers.');
        if(!window.confirm('Start INCI waterfall on up to '+config.limit+' verified canonical products? Only independently verified full INCI may publish.'))return;
        result=await post('/admin/waterfall/batch/start',{
          scope:config.scope,limit:Number(config.limit),dry_run:false,
          langsearch_enabled:config.langsearch_enabled,queue_jev:config.queue_jev,
          queue_gemini:config.queue_gemini,queue_inci_research:config.queue_inci_research,
          max_workers:Number(config.max_workers)
        });
        await save('running',result,result.task_id);setMessage('INCI waterfall started. Worker progress is currently process-local.');
        return result;
      }
      throw Error('This step is a review/quality gate, not an automatic mutation. Complete its human validation and save the result explicitly.');
    });
  }
  async function markReviewed(){
    await act(async()=>{
      if(!window.confirm('Record that you have reviewed this step? This does not automatically approve any identity or INCI evidence.'))return;
      await save('needs_review',{reviewed_at:new Date().toISOString(),evidence:preview||{}});
      setMessage('Review checkpoint saved. Individual unresolved products remain held.');
    });
  }
  async function next(){
    const idx=allSteps.findIndex(s=>s[0]===step);
    if(idx<allSteps.length-1)setStep(allSteps[idx+1][0]);
  }
  const entry=run?.steps?.find(s=>s.step_key===step);
  const currentIndex=allSteps.findIndex(s=>s[0]===step);
  const setField=(key,value)=>{setConfig(prev=>({...prev,[key]:value}));setPreview(null);};
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-2xl font-bold">{workflowKind==='offer_refresh'?'Prices & Stock Wizard':'Catalog Build Wizard'}</h2>
        <p className="text-sm text-slate-500">Persisted step settings, live jobs, independent review gates and explicit operator decisions.</p></div>
      <div className="flex gap-2"><select aria-label="Select saved workflow" className={INPUT} value={run?.id||''} onChange={e=>loadRun(e.target.value)}>
        <option value="">Select a saved workflow</option>{runs.map(r=><option key={r.id} value={r.id}>{r.title} · {r.id.slice(-6)}</option>)}</select>
        <button className={PRIMARY} disabled={busy} onClick={createRun}>New workflow</button></div>
    </div>
    {!run?<section className={PANEL}><h3 className="font-semibold">Start a guided run</h3>
      <p className="text-sm text-slate-500 my-3">Choose New workflow to save your configuration and return to it in future sessions.</p></section>:
    <div className="grid lg:grid-cols-[270px_minmax(0,1fr)] gap-5">
      <nav className={PANEL+' h-fit space-y-1'} aria-label="Wizard steps">
        <p className="text-xs text-slate-500 font-semibold mb-4 break-all">RUN {run.id}</p>
        {allSteps.map(([key,label],i)=>{const state=run.steps?.find(s=>s.step_key===key)?.status||'draft';
          return <button key={key} onClick={()=>selectStep(key)}
            className={`w-full p-3 text-left rounded-xl flex gap-3 items-start ${step===key?'bg-violet-100 text-violet-900':'hover:bg-slate-50'}`}>
            <span className={`w-7 h-7 rounded-full text-xs flex items-center justify-center shrink-0 ${state==='completed'?'bg-emerald-100':'bg-slate-100'}`}>{state==='completed'?<Check size={15}/>:i+1}</span>
            <span><span className="text-sm font-semibold block">{label}</span><span className="text-xs text-slate-500">{state.replaceAll('_',' ')}</span></span>
          </button>;})}
      </nav>
      <div className="space-y-4 min-w-0">
        <section className={PANEL}>
          <div className="flex justify-between flex-wrap items-center mb-2 gap-2">
            <div><span className="text-xs uppercase text-violet-700 font-bold">Step {currentIndex+1} of {allSteps.length}</span>
              <h3 className="text-xl font-bold mt-1">{allSteps[currentIndex]?.[1]}</h3></div>
            <span className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-semibold">{entry?.status||'draft'}</span>
          </div>
          <p className="text-sm text-slate-500 mb-5">{allSteps[currentIndex]?.[2]}</p>
          {step==='sources' && <div className="space-y-4">
            <p className="font-semibold text-sm">Enabled source registry</p>
            <div className="grid md:grid-cols-2 gap-2">{sources.map(s=><label key={s.id} className="flex items-start gap-3 border rounded-xl p-3">
              <input type="checkbox" className="mt-1" disabled={!s.enabled}
                checked={config.source_ids.includes(s.id)}
                onChange={e=>setField('source_ids',e.target.checked?[...config.source_ids,s.id]:config.source_ids.filter(k=>k!==s.id))}/>
              <span><b className="block text-sm">{s.name||s.id}</b><small className="text-slate-500">{s.adapter_type||s.platform||'source'} · {s.enabled?'enabled':'paused'}</small></span>
            </label>)}</div>
            <button className={BTN} onClick={()=>setField('source_ids',sources.filter(s=>s.enabled).map(s=>s.id))}>Select all enabled</button>
          </div>}
          {(step==='sources'||step==='discovery')&&<div className="grid md:grid-cols-2 gap-4 mt-4">
            <label className="text-sm">Start at page<input className={INPUT+' mt-1'} type="number" min="1" value={config.start_page} onChange={e=>setField('start_page',Math.max(1,Number(e.target.value)||1))}/></label>
            <label className="text-sm">{workflowKind==='offer_refresh'?'Page limit (required)':'Pages (0 = all pages)'}
              <input className={INPUT+' mt-1'} type="number" min={workflowKind==='offer_refresh'?'1':'0'} value={config.pages} onChange={e=>setField('pages',Math.max(workflowKind==='offer_refresh'?1:0,Number(e.target.value)||0))}/></label>
          </div>}
          {step==='discovery'&&<div className="mt-4 p-3 rounded-lg bg-slate-50 text-sm">
            <b>Selected sources:</b> {config.source_ids.join(', ')||'None; return to Sources'}
            {workflowKind==='offer_refresh'?<div className="flex gap-4 mt-3">
              <label><input type="checkbox" checked={config.update_price} onChange={e=>setField('update_price',e.target.checked)}/> Update price</label>
              <label><input type="checkbox" checked={config.update_stock} onChange={e=>setField('update_stock',e.target.checked)}/> Update stock</label></div>:
              <p className="mt-2">Sample preview scans one page per selected source in dry-run mode. Applying uses the configured page limit. Scan checkpoints are retained in backend task state.</p>}
          </div>}
          {(step==='identity'||step==='inci')&&<div className="grid md:grid-cols-2 gap-4">
            <label className="text-sm">Max products per run<input className={INPUT+' mt-1'} min="1" max="10000" type="number" value={config.limit} onChange={e=>setField('limit',Math.max(1,Math.min(10000,Number(e.target.value)||1)))}/></label>
            {step==='inci'&&<label className="text-sm">Scope<select value={config.scope} onChange={e=>setField('scope',e.target.value)} className={INPUT+' mt-1'}>
              <option value="unmatched_only">Missing verified INCI</option><option value="all_skincare">Revalidate all skincare</option></select></label>}
          </div>}
          {step==='inci'&&<div className="mt-4 grid sm:grid-cols-2 gap-3 text-sm">
            {[['langsearch_enabled','Enable LangSearch research'],['queue_jev','Stage Jev queue'],['queue_gemini','Stage Gemini INCI queue'],['queue_inci_research','Stage research queue']].map(([k,label])=>
              <label key={k} className="rounded-lg border p-3"><input className="mr-2" type="checkbox" checked={!!config[k]} onChange={e=>setField(k,e.target.checked)}/>{label}</label>)}
            <label>Concurrent INCI workers<input className={INPUT} min="1" max="12" type="number" value={config.max_workers} onChange={e=>setField('max_workers',Math.max(1,Math.min(12,Number(e.target.value)||1)))}/></label>
          </div>}
          {step==='classification'&&<div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl p-4 text-sm">
            Automatic classification still uses the legacy catalog tooling. Inspect current reviews and categories there; no unguarded bulk classifier has been wired to this wizard.</div>}
          {step==='inci_review'&&<div className="space-y-3"><p className="text-sm">Product-identity Gemini, INCI Jev/Gemini and human decisions are separate. Navigate to their queues and return to record this review checkpoint.</p>
            <div className="flex flex-wrap gap-3"><button className={BTN} onClick={()=>navigate('ai',{tab:'queues'})}>Open AI queues →</button><button className={BTN} onClick={()=>navigate('reviews')}>Open Review Workbench →</button></div></div>}
          {step==='quality'&&<div className="rounded-lg bg-amber-50 text-amber-900 p-4 text-sm">Quality preview shows identity and INCI readiness, not an automatic release certificate. Publication is not authorized merely by advancing the wizard.</div>}
          <div className="border-t pt-4 mt-6 flex gap-2 flex-wrap">
            <button className={BTN} disabled={busy} onClick={saveDraft}><Save size={15} className="inline mr-1"/>Save draft</button>
            <button className={BTN} disabled={busy} onClick={previewAction}><Search size={15} className="inline mr-1"/>Preview</button>
            {['sources','discovery','identity','inci'].includes(step)&&<button className={PRIMARY} disabled={busy} onClick={applyAction}>
              <Play size={15} className="inline mr-1"/>{step==='sources'?'Confirm sources':step==='discovery'?'Apply / start':step==='identity'?'Start rematch':'Start INCI run'}</button>}
            {['classification','inci_review','quality'].includes(step)&&<button className={BTN} disabled={busy} onClick={markReviewed}>Record review checkpoint</button>}
          </div>
        </section>
        {error&&<p role="alert" className="rounded-xl border border-red-200 bg-red-50 text-red-700 p-3 text-sm">{error}</p>}
        {message&&<p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-800 p-3 text-sm">{message}</p>}
        {preview&&<section className={PANEL}><h4 className="font-semibold">Current preview / evidence</h4>
          <p className="text-xs text-slate-500 mt-2">Preview type: {previewType||'saved'}; operations may change underlying data between preview and apply.</p>
          <pre className="text-xs bg-slate-50 p-3 mt-3 rounded-xl max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(preview,null,2)}</pre></section>}
        {runningJob&&<section className={PANEL}><h4 className="font-semibold">Linked job</h4><p className="text-sm font-mono break-all mt-2">{runningJob}</p>
          <p className="text-sm mt-2">The job continues on the backend after you leave this page. Check its current status in Job Center.</p>
          <button className={BTN+' mt-3'} onClick={()=>navigate('jobs')}><ExternalLink size={15} className="inline mr-2"/>Job Center</button></section>}
        <div className="flex justify-between gap-3"><button className={BTN} disabled={currentIndex===0} onClick={()=>selectStep(allSteps[currentIndex-1][0])}><ChevronLeft size={16} className="inline"/> Previous step</button>
          <button className={BTN} disabled={currentIndex>=allSteps.length-1} onClick={next}>Next step <ChevronRight size={16} className="inline"/></button></div>
      </div>
    </div>}
  </div>;
}
