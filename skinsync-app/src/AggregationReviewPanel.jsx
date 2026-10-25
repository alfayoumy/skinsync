import { apiFetch } from './apiFetch';
import { useCallback, useEffect, useRef, useState } from 'react';
import { TestResult } from './ConnectionTests';
import GeminiDecisionsTable from './GeminiDecisionsTable';
import useConnectionTests from './useConnectionTests';
const API = 'http://127.0.0.1:8000/admin/aggregation';
const categories = ['Cleanser', 'Moisturizer', 'Serum', 'Sunscreen', 'Toner', 'Exfoliator', 'Eye Skincare', 'Lip Care', 'Mask', 'Treatment', 'Body Care', 'Tanning', 'Set', 'Makeup', 'Hair Care', 'Fragrance'];
const field = 'w-full border border-gray-300 rounded-lg p-2 bg-white text-sm';
async function api(path, options = {}) {
  const response = await apiFetch(`${API}${path}`, { ...options, headers: { 'Content-Type': 'application/json' } });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail=typeof data?.detail==='string' ? data.detail : response.statusText;
    throw new Error(`${path}: API returned HTTP ${response.status}${detail ? ' — '+detail : ''}`);
  }
  if (!data) throw new Error(`${path}: API returned invalid or empty JSON.`);
  return data;
}
export default function AggregationReviewPanel({ onClose, initialSourceId = 'sourcebeauty', mode = 'sources' }) {
  const dialog = useRef(null);
  const diagnostics = useConnectionTests();
  const [sampleUrl, setSampleUrl] = useState('');
  const [sources, setSources] = useState([]), [reviews, setReviews] = useState([]), [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0), [kind, setKind] = useState(''), [selected, setSelected] = useState(null);
  const [sourceId, setSourceId] = useState(initialSourceId), [coverage, setCoverage] = useState(null);
  const [reviewer, setReviewer] = useState(''), [reason, setReason] = useState(''), [action, setAction] = useState('request_evidence');
  const [mergeSurvivor, setMergeSurvivor] = useState('');
  const [reviewView,setReviewView] = useState('manual');
  const [queueStatus,setQueueStatus] = useState(null);
  const [geminiTask,setGeminiTask] = useState(null);
  const [geminiJob,setGeminiJob] = useState(null);
  const [selectedReviewIds,setSelectedReviewIds] = useState([]);
  const [geminiBatchSize,setGeminiBatchSize] = useState(10);
  const [geminiLimit,setGeminiLimit] = useState(500);
  const [savedDecisionsPreview,setSavedDecisionsPreview] = useState(null);
  const [savedApplyTask,setSavedApplyTask] = useState(null);
  const [savedApplyJob,setSavedApplyJob] = useState(null);
  const [savedDecisionLimit,setSavedDecisionLimit] = useState(5000);
  const [savedDecisionThreshold,setSavedDecisionThreshold] = useState(0.90);
  const [canonicalId, setCanonicalId] = useState(''), [category, setCategory] = useState('Moisturizer');
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const [reviewsLoading,setReviewsLoading] = useState(false);
  const [reviewsError,setReviewsError] = useState('');
  const [rematchPreview,setRematchPreview]=useState(null);
  const [rematchDiagnosis,setRematchDiagnosis]=useState(null);
  const [rematchJob,setRematchJob]=useState(null);
  const [rematchBusy,setRematchBusy]=useState(false);
  const [blockingTaskDetails,setBlockingTaskDetails]=useState(null);
  const [rematchLimit,setRematchLimit]=useState(500);
  const [weakPreview,setWeakPreview]=useState(null);
  const [weakBusy,setWeakBusy]=useState(false);
  const [geminiError,setGeminiError] = useState('');
  const [sourceRequeuePreview,setSourceRequeuePreview]=useState(null);
  const [sourceRequeueBusy,setSourceRequeueBusy]=useState(false);
  async function previewSourceGeminiRequeue(){
    setSourceRequeueBusy(true);setError('');
    try{setSourceRequeuePreview(await api('/reviews/gemini/requeue/preview'))}
    catch(e){setError(e.message)}
    finally{setSourceRequeueBusy(false)}
  }
  async function applySourceGeminiRequeue(){
    if(!sourceRequeuePreview?.can_apply)return;
    const confirmation=window.prompt(
      'This archives the old IDENTITY Gemini responses in a NEW SQLite backup, then clears only the live identity queue and requeues '+sourceRequeuePreview.requeueable_review_cases+
      ' unresolved saved-source comparisons. Already rematched products, reviews and INCI stay untouched. Type RESET IDENTITY GEMINI AND REQUEUE to proceed.');
    if(confirmation!=='RESET IDENTITY GEMINI AND REQUEUE')return;
    setSourceRequeueBusy(true);setError('');
    try{
      const result=await api('/reviews/gemini/requeue',{
        method:'POST',body:JSON.stringify({fingerprint:sourceRequeuePreview.fingerprint,confirmation})
      });
      setMessage('New identity Gemini queue: '+result.new_identity_queue_count+
        ' saved-source cases. Old identity queue archived to '+result.backup_path+
        '. Gemini has NOT been called. Start a small Gemini run when ready.');
      setSourceRequeuePreview(null);
      await refresh();
    }catch(e){setError(e.message);setSourceRequeuePreview(null)}
    finally{setSourceRequeueBusy(false)}
  }
  async function previewWeakSuggestions() {
    setWeakBusy(true);setError('');
    try{setWeakPreview(await api('/reviews/weak-suggestions/preview'))}
    catch(e){setError(e.message)}
    finally{setWeakBusy(false)}
  }
  async function deferWeakSuggestions() {
    if(!weakPreview?.eligible)return;
    if(!window.confirm('Move '+weakPreview.eligible+' weak, unevaluated identity suggestions to Awaiting stronger evidence? Source observations stay saved; no product identities are linked or merged and no INCI is changed.'))return;
    setWeakBusy(true);setError('');
    try{
      const result=await api('/reviews/weak-suggestions/defer',{
        method:'POST',body:JSON.stringify({
          fingerprint:weakPreview.fingerprint,
          confirmation:'DEFER WEAK MATCH SUGGESTIONS',limit:weakPreview.limit})});
      setMessage(result.deferred+' unsupported identity suggestions preserved in Awaiting stronger evidence. No products, formulas or source observations changed.');
      setWeakPreview(null);setOffset(0);setSelected(null);
      await refresh();
    }catch(e){setError(e.message);setWeakPreview(null)}
    finally{setWeakBusy(false)}
  }
  async function previewIdentityRematch(){
    setRematchBusy(true);setError('');
    try{
      const preview=await api('/reviews/rematch/preview?limit='+Number(rematchLimit));
      setRematchPreview(preview);
    }catch(e){setError(e.message)}
    finally{setRematchBusy(false)}
  }
  async function inspectBlockingTask(taskId){
    setRematchBusy(true);setError('');
    try{
      const response=await apiFetch('http://127.0.0.1:8000/tasks/'+encodeURIComponent(taskId)+'?include_subtasks=false&log_limit=10');
      const data=await response.json().catch(()=>null);
      if(!response.ok)throw new Error(data?.detail||'Could not inspect blocking task.');
      setBlockingTaskDetails(data);
    }catch(e){setError(e.message)}
    finally{setRematchBusy(false)}
  }
  async function cancelBlockingJob(job){
    let url=null;
    const id=encodeURIComponent(job.task_id);
    if(job.task_type==='catalog_discovery')url='/admin/workflows/products/discover-multiple/'+id+'/cancel';
    if(job.task_type==='product_identity_gemini')url='/admin/aggregation/reviews/gemini/jobs/'+id+'/cancel';
    if(job.task_type==='apply_saved_gemini')url='/admin/aggregation/reviews/gemini/saved/jobs/'+id+'/cancel';
    if(job.task_type==='saved_observation_identity_rematch')url='/admin/aggregation/reviews/rematch/jobs/'+id+'/cancel';
    if(!url)return;
    if(!window.confirm('Request cancellation for '+job.task_type+' '+job.task_id+'? The worker may finish its current unit before stopping.'))return;
    setRematchBusy(true);setError('');
    try{
      const response=await apiFetch('http://127.0.0.1:8000'+url,{method:'POST',
        headers:{'Content-Type':'application/json'}});
      const result=await response.json().catch(()=>null);
      if(!response.ok)throw new Error(result?.detail||'Unable to request cancellation.');
      setMessage('Cancellation requested for '+job.task_id+'. Refresh the rematch preview after the worker exits.');
      await previewIdentityRematch();
    }catch(e){setError(e.message)}
    finally{setRematchBusy(false)}
  }
  async function diagnoseIdentityRematch(){
    setRematchBusy(true);setError('');
    try{setRematchDiagnosis(await api('/reviews/rematch/diagnose?limit=50'))}
    catch(e){setError(e.message)}
    finally{setRematchBusy(false)}
  }
  async function startIdentityRematch(){
    if(!rematchPreview?.can_start)return;
    if(!window.confirm('Recalculate '+rematchPreview.eligible_products+
      ' saved products against the CURRENT catalog? A full database backup is taken first. Stale reviews are closed ONLY after valid source-backed rematch; no shops or Gemini will be called and no INCI is published.'))return;
    setRematchBusy(true);setError('');
    try{
      const job=await api('/reviews/rematch/start',{
        method:'POST',body:JSON.stringify({
          fingerprint:rematchPreview.fingerprint,
          limit:rematchPreview.limit,
          confirmation:'REMATCH SAVED PRODUCTS'})});
      setRematchJob({...job,task_id:job.task_id});
      setRematchPreview(null);
    }catch(e){setError(e.message)}
    finally{setRematchBusy(false)}
  }
  async function stopIdentityRematch(){
    if(!rematchJob?.task_id)return;
    setRematchBusy(true);setError('');
    try{
      await api('/reviews/rematch/jobs/'+encodeURIComponent(rematchJob.task_id)+'/cancel',
                {method:'POST'});
    }catch(e){setError(e.message)}
    finally{setRematchBusy(false)}
  }
  useEffect(()=>{
    // Jobs are server-owned. Reopening Product Reviews must recover control.
    let active=true;
    const poll=()=>api('/reviews/rematch/active').then(report=>{
      if(!active)return;
      const latest=report.jobs?.[0];
      if(latest)setRematchJob(latest);
    }).catch(()=>{});
    poll();const id=setInterval(poll,2200);
    return ()=>{active=false;clearInterval(id)};
  },[]);
    const refresh = useCallback(async () => {
    // Source settings, saved review records and the optional Gemini status
    // must not be a single all-or-nothing Promise.all.
    if (mode === 'sources') {
      const registry=await api('/sources');
      setSources(registry.sources || []);
      return;
    }
    setReviewsLoading(true);
    try {
      const [casesResult,geminiResult]=await Promise.allSettled([
        api(`/reviews?status=pending&view=${reviewView}&offset=${offset}&limit=50${kind ? `&kind=${kind}` : ''}`),
        api('/reviews/gemini/queue')
      ]);
      if (casesResult.status === 'fulfilled') {
        setReviews(casesResult.value.reviews || []);
        setTotal(casesResult.value.total || 0);
        setSelectedReviewIds([]);
        setReviewsError('');
      } else {
        setReviewsError('Could not load saved product reviews: '+casesResult.reason.message+
          '. Check that the API is running on port 8000, then retry.');
      }
      if (geminiResult.status === 'fulfilled') {
        setQueueStatus(geminiResult.value);
        setGeminiError('');
      } else {
        setQueueStatus(null);
        setGeminiError('Gemini queue status is temporarily unavailable: '+
          geminiResult.reason.message+'. Existing review cases can still be examined.');
      }
    } finally {setReviewsLoading(false);}
  }, [mode, offset, kind, reviewView]);
  useEffect(() => { dialog.current.showModal(); }, []);
  useEffect(() => { refresh().catch(e => setError(e.message)); }, [refresh]);
  useEffect(()=>{
    if(!geminiTask)return;
    let active=true;
    async function poll() {
      try {
        const job=await api('/reviews/gemini/jobs/'+encodeURIComponent(geminiTask));
        if(!active)return;
        setGeminiJob(job);
        if(!['running','pending'].includes(job.status)){
          setGeminiTask(null);
          refresh().catch(e=>setError(e.message));
        }
      }catch(e){if(active){setError(e.message);setGeminiTask(null);}}
    }
    poll();const timer=setInterval(poll,2000);
    return ()=>{active=false;clearInterval(timer)};
  },[geminiTask,refresh]);
  useEffect(()=>{
    if(!savedApplyTask)return;
    let active=true;
    const poll=async()=>{
      try {
        const job=await api('/reviews/gemini/saved/jobs/'+encodeURIComponent(savedApplyTask));
        if(!active)return;
        setSavedApplyJob(job);
        if(!['running','pending'].includes(job.status)){
          setSavedApplyTask(null);
          setSavedDecisionsPreview(null);
          refresh().catch(e=>setError(e.message));
        }
      } catch(e){if(active){setSavedApplyTask(null);setError(e.message);}}
    };
    poll();
    const timer=setInterval(poll,1700);
    return ()=>{active=false;clearInterval(timer)};
  },[savedApplyTask,refresh]);
  async function previewSavedDecisions() {
    await perform(async()=>{
      const report=await api('/reviews/gemini/saved/preview',{
        method:'POST',body:JSON.stringify({
          limit:Number(savedDecisionLimit),confidence_threshold:Number(savedDecisionThreshold)
        })
      });
      setSavedDecisionsPreview(report);
      setMessage('Preview uses saved Gemini verdicts only — no API calls, no database changes.');
    });
  }
  async function applySavedDecisions() {
    if(!window.confirm('Apply existing Gemini decisions without any model calls? Safe matches will be verified; safe distinct formulation groups will be split, preserving all retailer listings. Unresolved cases stay for review. A preview is recommended but not required.'))return;
    await perform(async()=>{
      const started=await api('/reviews/gemini/saved/apply',{
        method:'POST',body:JSON.stringify({
          limit:Number(savedDecisionLimit),confidence_threshold:Number(savedDecisionThreshold)
        })
      });
      setSavedApplyTask(started.task_id);
      setSavedApplyJob({status:'running',processed_items:0,total_items:started.saved_decisions});
      setMessage('Applying '+started.saved_decisions+' stored Gemini decisions. Zero new Gemini requests.');
    });
  }
  async function stopSavedDecisions() {
    if(!savedApplyTask)return;
    await perform(async()=>{
      await api('/reviews/gemini/saved/jobs/'+encodeURIComponent(savedApplyTask)+'/cancel',{method:'POST'});
      setMessage('Stop requested. Already-applied results remain saved.');
    });
  }
  async function sendToGemini(reviewIds) {
    await perform(async()=>{
      const payload=reviewIds?{review_ids:reviewIds,limit:reviewIds.length}:{limit:20000};
      const res=await api('/reviews/gemini/enqueue',{method:'POST',body:JSON.stringify(payload)});
      setMessage(res.queued+' comparable identity cases added to Gemini. '+(res.skipped_unresearchable||0)+' unsupported reviews skipped; no model calls were made.');
      setReviewView('awaiting_gemini');setOffset(0);
    });
  }
  async function removeUnsupportedGeminiCases() {
    await perform(async()=>{
      const report=await api('/reviews/gemini/retire-unresearchable',{method:'POST'});
      setMessage(report.retired_to_manual_review+' cases moved to human review. No Gemini calls made; existing evidence and decisions preserved.');
      setReviewView('manual');setOffset(0);
    });
  }
  async function startGemini() {
    await perform(async()=>{
      const res=await api('/reviews/gemini/run',{method:'POST',body:JSON.stringify({
        limit:Number(geminiLimit),batch_size:Number(geminiBatchSize)
      })});
      setGeminiTask(res.task_id);setGeminiJob({status:'running',processed_items:0,total_items:res.queued});
      setMessage('Gemini identity review started. Several cases share one request.');
    });
  }
  async function stopGemini() {
    if(!geminiTask)return;
    await perform(async()=>{
      await api('/reviews/gemini/jobs/'+encodeURIComponent(geminiTask)+'/cancel',{method:'POST'});
      setMessage('Gemini stop requested; unresolved cases remain in the queue.');
    });
  }
  async function perform(callback) {
    setBusy(true); setError(''); setMessage('');
    try { await callback(); await refresh(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const source = sources.find(s => s.id === sourceId);
  const canLink = selected?.source_id && selected.source_id !== 'canonical';
  return <dialog ref={dialog} aria-label={mode === 'sources' ? 'Product sources' : 'Product reviews'} onCancel={e => { e.preventDefault(); onClose(); }} className="m-auto w-[min(1160px,96vw)] rounded-2xl p-0 shadow-2xl backdrop:bg-black/50">
    <header className="p-5 border-b flex justify-between gap-4"><div><h2 className="text-xl font-bold">{mode === 'sources' ? 'Product sources' : 'Product matching reviews'}</h2>
      <p className="text-sm text-gray-500">{mode === 'sources' ? 'Manage which websites SkinSync can collect from, test connections, and inspect source coverage.' : 'Examine uncertain or conflicting products and save an explicit decision. Source settings are managed separately.'}</p></div><button className="border rounded-lg px-3" onClick={onClose}>Close</button></header>
    <main className="p-5 space-y-5 bg-gray-50 overflow-y-auto max-h-[78vh]">
      {error && <p role="alert" className="bg-red-50 text-red-800 p-3 rounded-lg">{error}</p>}
      {mode==='reviews'&&reviewsError&&<div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
        {reviewsError}
        <button type="button" className="ml-3 underline font-semibold" onClick={()=>refresh().catch(e=>setError(e.message))}>Retry loading reviews</button>
      </div>}
      {mode==='reviews'&&geminiError&&<div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
        {geminiError}
        <button type="button" className="ml-3 underline font-semibold" onClick={()=>refresh().catch(e=>setError(e.message))}>Retry Gemini status</button>
      </div>}
      {message && <p role="status" className="bg-green-50 text-green-800 p-3 rounded-lg">{message}</p>}
      {mode === 'sources' && <>
      <section aria-label="Registered sources" className="bg-white border rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Product sources ({sources.length})</h3><button className="border rounded px-3 py-1" disabled={diagnostics.running.some(k => k.startsWith('source:'))} onClick={() => diagnostics.test('source')}>Test all sources</button>
          <p className="text-xs text-gray-500">{sources.filter(s => s.enabled).length} collection enabled · {sources.filter(s => !s.enabled).length} staged</p>
        </div>
        <p className="text-xs text-gray-500">Tests check one sample without saving products or enabling publication. Results last until server restart.</p>{diagnostics.error && <p role="alert">{diagnostics.error}</p>}<div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
          {sources.map(s => <div key={s.id} className="border rounded-xl p-2"><button type="button" aria-pressed={sourceId === s.id}
            onClick={() => { setSourceId(s.id); setCoverage(null); }}
            className={`rounded-xl border p-3 text-left transition-colors ${sourceId === s.id ? 'border-purple-600 bg-purple-50' : 'border-gray-200 bg-gray-50 hover:border-purple-300'}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-bold text-gray-900">{s.name}</span>
              <span aria-label={s.enabled ? 'Collection enabled' : 'Staged'} className={`h-2 w-2 rounded-full shrink-0 ${s.enabled ? 'bg-emerald-500' : 'bg-amber-400'}`} />
            </div>
            <p className="text-[10px] text-gray-500 capitalize mt-1">{s.source_type} · {s.market} · {s.enabled ? 'Enabled' : 'Staged'}</p>
            <p className="text-lg font-bold text-purple-700 mt-1">{(s.total_products || 0).toLocaleString()} <span className="text-[10px] font-normal text-gray-500">products</span></p>
          </button><button className="border rounded px-3 py-1 mt-2 text-xs" disabled={diagnostics.running.includes(`source:${s.id}`)} onClick={() => diagnostics.test('source', [s.id])}>Test source</button><button type="button" role="switch" aria-checked={Boolean(s.enabled)} aria-label={`Collection for ${s.name}`} disabled={busy} className={`block w-full mt-2 rounded-lg border px-2 py-1 text-xs ${s.enabled ? 'border-emerald-400 bg-emerald-50 text-emerald-800' : 'border-gray-300 text-gray-600'}`} onClick={() => perform(async () => { await api(`/sources/${s.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !s.enabled }) }); setMessage(`${s.name}: collection ${s.enabled ? 'paused' : 'enabled'}.`); })}>Collection {s.enabled ? 'on — pause' : 'off — enable'}</button><TestResult result={diagnostics.results[`source:${s.id}`]} running={diagnostics.running.includes(`source:${s.id}`)} /></div>)}
        </div>
      </section>
      <section className="bg-white border rounded-xl p-4 space-y-3"><h3 className="font-semibold">Source coverage</h3><div className="flex flex-wrap items-center gap-3">
        <select aria-label="Source" className="border p-2 rounded-lg" value={sourceId} onChange={e => { setSourceId(e.target.value); setCoverage(null); }}>{sources.map(s => <option key={s.id} value={s.id}>{s.name} · {s.market}</option>)}</select><span className="text-sm text-gray-500">{source?.publish_enabled ? 'Publication approved' : 'Publication staged'}</span>
        <button disabled={busy} className="border rounded-lg px-3 py-2" onClick={() => perform(async () => setCoverage(await api(`/sources/${sourceId}/coverage`)))}>View coverage</button>
        <button disabled={busy || !source} className="border rounded-lg px-3 py-2" onClick={() => perform(async () => { await api(`/sources/${sourceId}`, { method: 'PATCH', body: JSON.stringify({ enabled: !source.enabled }) }); })}>{source?.enabled ? 'Pause collection' : 'Enable staged collection'}</button>
      </div><div className="flex gap-2"><input aria-label="Diagnostic product URL" placeholder="Product URL for selected source" type="url" className={field} value={sampleUrl} onChange={e => setSampleUrl(e.target.value)} /><button disabled={!sampleUrl || diagnostics.running.includes(`source:${sourceId}`)} className="border rounded px-3 shrink-0" onClick={() => diagnostics.test('source', [sourceId], sampleUrl)}>Test product URL</button></div>{coverage && <dl className="grid grid-cols-2 md:grid-cols-4 gap-3">{[['Products', coverage.discovered_products], ['Canonical matches', coverage.uniquely_matched_canonicals], ['Offers', coverage.additional_offers], ['Full INCI candidates', coverage.validated_full_inci_products]].map(([label, value]) => <div key={label} className="bg-gray-50 p-3 rounded-lg"><dt className="text-sm text-gray-500">{label}</dt><dd className="text-xl font-bold">{value}</dd></div>)}</dl>}</section>
      </>}
      {mode === 'reviews' && <div className="rounded-xl border bg-white p-4 flex flex-wrap gap-3 items-center justify-between">
        <p className="text-sm text-gray-600 max-w-2xl">Only saved review cases appear here. A preview does not create cases. Find genuine legacy catalog discrepancies here, without approving any product automatically.</p>
        <button type="button" disabled={busy} className="rounded-lg bg-purple-700 px-4 py-2 text-white text-sm font-semibold disabled:opacity-50"
          onClick={()=>perform(async()=>{
            const response=await apiFetch('http://127.0.0.1:8000/admin/workflows/products/prepare-reviews',{
              method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
            });
            const data=await response.json();
            if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Could not prepare review cases');
            setMessage(data.review_cases_prepared ? (data.review_cases_prepared+' new cases added. '+data.pending_review_cases+' cases are now pending.') : ('No new cases to create. '+data.pending_review_cases+' review cases are already saved and waiting.'));
          })}>
          {busy?'Checking products...':'Find legacy products needing review'}
        </button>
      </div>}
      {mode === 'reviews' && <section className="rounded-xl border bg-white p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h3 className="font-semibold">Gemini product matching</h3>
            <p className="text-xs text-gray-600">Gemini checks multiple retailer product identities in a single call. Clear matches are approved only when independent variant, brand and INCI checks also pass. All other cases stay available for a person.</p></div>
          <span className="text-xs font-semibold text-purple-800">{queueStatus?.pending_identity_cases??'—'} unresolved identity/duplicate cases</span>
        </div>
        <div className="rounded-lg border border-purple-200 p-3 space-y-2 text-xs">
          <strong>Rebuild Gemini queue from failed saved-source matches</strong>
          <p className="text-gray-600">Preview fresh comparisons against current canonical products, archive all old identity Gemini responses to a full database backup, and replace the identity queue. The INCI Gemini queue and successfully rematched products are unchanged. This never runs Gemini automatically.</p>
          <button type="button" className="rounded-lg border px-3 py-2 font-semibold disabled:opacity-50"
            disabled={sourceRequeueBusy||busy||Boolean(geminiTask)} onClick={previewSourceGeminiRequeue}>
            Preview reset and requeue — no changes
          </button>
          {sourceRequeuePreview&&<div className="rounded-lg bg-gray-50 p-3 space-y-1">
            <p><strong>{sourceRequeuePreview.requeueable_review_cases}</strong> fresh source/canonical comparisons ready ·
              {' '}{sourceRequeuePreview.old_queue_count} old identity queue entries will be archived and cleared</p>
            <p>Remaining saved-source reviews: {sourceRequeuePreview.remaining_review_cases} ·
              skipped: {Object.entries(sourceRequeuePreview.skipped||{}).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · ')||'none'}</p>
            {!sourceRequeuePreview.can_apply&&<p className="text-red-700">The queue cannot be rebuilt yet. Check for active discovery, rematch, Gemini or maintenance jobs before retrying.</p>}
            <button type="button" className="rounded-lg border border-purple-700 px-3 py-2 font-bold disabled:opacity-50"
              disabled={!sourceRequeuePreview.can_apply||sourceRequeueBusy||Boolean(geminiTask)}
              onClick={applySourceGeminiRequeue}>
              Back up, clear old identity responses and requeue
            </button>
          </div>}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
          {[['Waiting for Gemini',queueStatus?.counts?.queued||0],
            ['Processing',queueStatus?.counts?.processing||0],
            ['Resolved automatically',queueStatus?.counts?.auto_resolved||0],
            ['Need a person',queueStatus?.counts?.needs_manual||0]].map(([label,value])=>
            <div key={label} className="rounded-lg bg-gray-50 p-2"><strong className="text-lg block">{value}</strong>{label}</div>)}
        </div>
        {queueStatus?.readiness&&<div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 space-y-1">
          <strong>{queueStatus.readiness.queued_actionable} researchable queued cases · {queueStatus.readiness.queued_without_comparable_evidence} unsupported queued cases</strong>
          <p>Fresh provisional identities often have no canonical anchor or second retailer listing. Gemini cannot establish an identity match from that information alone; those cases remain for human review.</p>
          {queueStatus.readiness.queued_without_comparable_evidence>0&&
            <button type="button" className="mt-2 rounded-lg border border-amber-400 bg-white px-3 py-2 font-semibold disabled:opacity-50"
              disabled={busy||Boolean(geminiTask)} onClick={removeUnsupportedGeminiCases}>
              Move unsupported cases to manual review — 0 Gemini calls
            </button>}
        </div>}
        <div className="flex gap-2 flex-wrap items-center">
          <button type="button" disabled={busy||Boolean(geminiTask)} onClick={()=>sendToGemini(null)}
            className="rounded-lg bg-purple-700 text-white px-3 py-2 text-sm font-semibold disabled:opacity-50">
            Send all unresolved identity cases to Gemini
          </button>
          <button type="button" disabled={busy||!selectedReviewIds.length||Boolean(geminiTask)}
            onClick={()=>sendToGemini(selectedReviewIds)}
            className="rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-50">
            Send selected ({selectedReviewIds.length})
          </button>
          <label className="text-xs font-semibold">Cases per Gemini call
            <select value={geminiBatchSize} onChange={e=>setGeminiBatchSize(Number(e.target.value))} className="border rounded p-2 ml-2">
              {[5,10,20].map(n=><option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold">Cases per run
            <input type="number" min="1" max="20000" value={geminiLimit} onChange={e=>setGeminiLimit(e.target.value)}
              className="border rounded p-2 ml-2 w-24"/>
          </label>
          <button type="button" disabled={busy||Boolean(geminiTask)||!(queueStatus?.readiness?.queued_actionable>0)}
            onClick={startGemini} className="rounded-lg border border-purple-600 bg-purple-50 text-purple-800 px-3 py-2 font-semibold text-sm disabled:opacity-50">
            Run Gemini matching
          </button>
          {geminiTask&&<button type="button" onClick={stopGemini} className="rounded-lg border border-red-300 text-red-700 px-3 py-2 text-sm font-semibold">Stop after current batch</button>}
        </div>
        {geminiJob&&<p role="status" className="text-xs text-gray-700">
          Job: {geminiJob.status} · {geminiJob.processed_items||0} evaluated ·
          {geminiJob.metadata?.stats?.auto_resolved||0} automatically approved ·
          {geminiJob.metadata?.stats?.needs_manual||0} awaiting human decision ·
          {geminiJob.metadata?.api_calls||0} API calls
          {geminiJob.error_message&&<span className="text-red-700"> · {geminiJob.error_message}</span>}
        </p>}
        <p className="text-xs text-gray-500">This is separate from Gemini's INCI queue. It never writes ingredient lists, changes prices, or automatically merges two different canonical product IDs.</p>
      </section>}
      {mode === 'reviews' && <section className="rounded-xl border border-purple-200 bg-purple-50 p-4 space-y-3">
        <div>
          <h3 className="font-bold text-gray-900">Apply Gemini decisions already saved</h3>
          <p className="text-sm text-gray-700 mt-1">No need to rerun thousands of products or spend more Gemini calls. Recheck the verdicts stored in the database, apply safe same-product matches automatically, and split clearly distinct product variants where the listings prove which items belong together. Unresolved structural conflicts remain for review.</p>
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-xs font-semibold">Minimum Gemini confidence
            <select className="block border rounded-lg p-2 bg-white mt-1" value={savedDecisionThreshold}
              onChange={e=>{setSavedDecisionThreshold(Number(e.target.value));setSavedDecisionsPreview(null);}}>
              {[0.90,0.92,0.95,0.97,0.99].map(v=><option key={v} value={v}>{Math.round(v*100)}%</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold">Maximum saved cases
            <input className="block border rounded-lg p-2 bg-white mt-1 w-28"
              type="number" min="1" max="20000" value={savedDecisionLimit}
              onChange={e=>{setSavedDecisionLimit(e.target.value);setSavedDecisionsPreview(null);}}/>
          </label>
          <button type="button" disabled={busy||Boolean(savedApplyTask)||Boolean(geminiTask)}
            onClick={previewSavedDecisions} className="rounded-lg border border-purple-300 bg-white px-4 py-2 text-sm font-semibold disabled:opacity-50">
            Preview saved decisions
          </button>
          <button type="button" disabled={busy||Boolean(savedApplyTask)||Boolean(geminiTask)}
            onClick={applySavedDecisions} className="rounded-lg bg-purple-700 px-4 py-2 text-white text-sm font-semibold disabled:opacity-50">
            Apply confident decisions — no Gemini calls
          </button>
          {savedApplyTask&&<button type="button" className="rounded-lg border border-red-300 px-3 py-2 text-sm text-red-700"
            onClick={stopSavedDecisions}>Stop application</button>}
        </div>
        {savedDecisionsPreview&&<div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs" role="status">
          {[
            ['Stored cases checked',savedDecisionsPreview.stored_decisions_checked],
            ['Would verify matches',savedDecisionsPreview.automatically_verified],
            ['Would split mixed cases',savedDecisionsPreview.automatically_split],
            ['New canonical products',savedDecisionsPreview.new_canonical_products??0],
            ['Still need review',savedDecisionsPreview.still_needs_review]
          ].map(([label,value])=><div key={label} className="rounded-lg border bg-white p-3"><strong className="block text-lg">{value}</strong>{label}</div>)}
        </div>}
        {savedDecisionsPreview?.sample_results?.length>0&&<details className="bg-white border rounded-lg p-3">
          <summary className="font-semibold text-xs cursor-pointer">Example application decisions and reasons</summary>
          <div className="max-h-56 overflow-auto space-y-1 mt-2 text-xs">
            {savedDecisionsPreview.sample_results.map(item=><p key={item.review_id}>
              Review #{item.review_id}: {item.verdict} ({Math.round((item.confidence||0)*100)}%) —
              <strong> {item.action}</strong>. {item.reason}
              {item.proposed_groups?.length>0&&<span className="block ml-3 mt-1 text-purple-900">
                {item.proposed_groups.length} distinct product groups — {item.new_canonical_products} new canonical(s):
                {item.proposed_groups.map((g,i)=><span key={i} className="block ml-3">
                  {g.retained?'Keep current canonical':'New canonical'} · {g.variant.join(', ')} ·
                  {g.product_ids.length} store listings (IDs: {g.product_ids.join(', ')})
                </span>)}
              </span>}
            </p>)}
          </div>
        </details>}
        {savedApplyJob&&<p role="status" className="rounded-lg bg-white p-3 text-sm">
          Application {savedApplyJob.status} · {savedApplyJob.processed_items||0}/{savedApplyJob.total_items||0} saved verdicts processed ·
          {savedApplyJob.metadata?.automatically_verified??savedApplyJob.metadata?.stats?.verified??0} verified ·
          {savedApplyJob.metadata?.automatically_split??savedApplyJob.metadata?.stats?.split??0} split cases ·
          {savedApplyJob.metadata?.new_canonical_products??savedApplyJob.metadata?.stats?.new_canonical_products??0} new canonicals ·
          {savedApplyJob.metadata?.still_needs_review??savedApplyJob.metadata?.stats?.manual??0} held for review ·
          <strong> 0 Gemini calls</strong>
          {savedApplyJob.error_message&&<span className="text-red-700"> · {savedApplyJob.error_message}</span>}
        </p>}
      </section>}
      {mode === 'reviews' && <GeminiDecisionsTable refreshKey={String(savedApplyTask||'')+'-'+String(savedApplyJob?.status||'')+'-'+String(geminiJob?.status||'')} />}
      {mode === 'reviews' && <section className="rounded-xl border bg-white p-4 space-y-3 mb-4">
        <h3 className="font-bold">Re-match existing products — no rescraping or Gemini</h3>
        <p className="text-sm text-gray-600">Old identity reviews with empty Gemini comparison lists are not decisions. Re-check the original saved retailer observations against current canonical products. No defensible match creates a new provisional canonical. Only successfully rematched cases leave the old review backlog. All source evidence is retained; no ingredients are republished.</p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs font-semibold">Saved products per batch
            <input type="number" min={1} max={10000} className={field+' w-28'}
              value={rematchLimit}
              onChange={e=>{setRematchLimit(e.target.value);setRematchPreview(null)}}/>
          </label>
          <button type="button" className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-50"
            disabled={rematchBusy||Number(rematchLimit)<1||Number(rematchLimit)>10000}
            onClick={previewIdentityRematch}>Preview current backlog</button>
          <button type="button" className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-50"
            disabled={rematchBusy} onClick={diagnoseIdentityRematch}>Diagnose next 50 — no changes</button>
        </div>
        {rematchPreview&&<div className="rounded-lg border p-3 text-xs space-y-2">
          <p><strong>{rematchPreview.eligible_products}</strong> saved products eligible ·
            {' '}{rematchPreview.review_cases_replaced_on_success} stale review records can be replaced if all recover.</p>
          <p>Excluded from automatic processing: {Object.entries(rematchPreview.excluded_reasons||{}).map(([k,v])=>k.replaceAll('_',' ')+': '+v).join(' · ')||'none'}</p>
          {!rematchPreview.can_start&&<div className="rounded-lg border border-amber-300 p-3 space-y-2">
            <strong className="text-amber-900">Why re-matching is blocked</strong>
            {(rematchPreview.active_jobs||[]).length>0&&<>
              <p>A job is still marked as executing. Its task ID and stage are shown below.
                A non-updating timestamp does not prove its worker has stopped.</p>
              {(rematchPreview.active_jobs||[]).map(job=><div key={job.task_id} className="rounded border p-2 space-y-2">
                <p><strong>{job.task_type}</strong> · {job.status} · {job.current_stage||'stage not recorded'}</p>
                <p className="break-all">Task ID: {job.task_id}</p>
                <p>Last recorded update: {job.updated_at||'unknown'}</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="rounded border px-2 py-1 disabled:opacity-50"
                    disabled={rematchBusy} onClick={()=>inspectBlockingTask(job.task_id)}>Inspect task and logs</button>
                  {['running','pending'].includes(job.status)&&
                   ['catalog_discovery','product_identity_gemini','apply_saved_gemini',
                    'saved_observation_identity_rematch'].includes(job.task_type)&&
                    <button type="button" className="rounded border border-red-300 px-2 py-1 text-red-700 disabled:opacity-50"
                      disabled={rematchBusy} onClick={()=>cancelBlockingJob(job)}>Request safe cancellation</button>}
                </div>
              </div>)}
            </>}
            {(rematchPreview.active_workers||[]).length>0&&<p className="text-red-700">
              Active worker indicators: {(rematchPreview.active_workers||[]).join(', ')}.
              Inspect or stop their running tasks before re-matching.</p>}
            {rematchPreview.maintenance_locked&&<p className="text-red-700">
              Catalog maintenance or backup restore is locked. Finish or cancel that operation first.</p>}
            {blockingTaskDetails&&<details open className="rounded border p-2">
              <summary className="cursor-pointer font-semibold">Task inspection: {blockingTaskDetails.task_id}</summary>
              <p>Status: {blockingTaskDetails.status} · stage: {blockingTaskDetails.current_stage||'none'}</p>
              <p>Started: {blockingTaskDetails.started_at||'unknown'} · updated: {blockingTaskDetails.updated_at||'unknown'}</p>
              {blockingTaskDetails.error_message&&<p className="text-red-700">{blockingTaskDetails.error_message}</p>}
              {(blockingTaskDetails.logs||[]).map((log,i)=><p key={i} className="break-words">{log.level}: {log.message}</p>)}
            </details>}
            <button type="button" className="rounded border px-3 py-2 font-bold disabled:opacity-50"
              disabled={rematchBusy} onClick={previewIdentityRematch}>Recheck blockers</button>
          </div>}
          {rematchPreview.idle_pending_records>0&&<p className="text-gray-600">
            {rematchPreview.idle_pending_records} task records are queued but never started; they do not block rematching.</p>}
          {rematchPreview.old_cancelled_records>0&&<p className="text-gray-600">
            {rematchPreview.old_cancelled_records} old cancelled task records are not treated as live workers.</p>}
          {rematchPreview.orphaned_gemini_processing_rows>0&&<p className="text-gray-600">
            {rematchPreview.orphaned_gemini_processing_rows} Gemini processing rows belong to nonrunning tasks; their historical state is preserved, but they do not block rematching.</p>}
          {rematchPreview.can_start&&<button type="button"
            className="rounded-lg bg-purple-800 px-3 py-2 text-white font-bold disabled:opacity-50"
            disabled={rematchBusy}
            onClick={startIdentityRematch}>Back up and re-match {rematchPreview.eligible_products} saved products</button>}
        </div>}
        {rematchDiagnosis&&<div className="rounded-lg border p-3 text-xs space-y-2">
          <p><strong>Read-only replay test:</strong> {rematchDiagnosis.recovered_products} of
            {' '}{rematchDiagnosis.eligible_products} would rematch; {rematchDiagnosis.failed_products} would fail.</p>
          {(rematchDiagnosis.error_groups||[]).map((r,i)=><p className="text-red-700" key={i}>
            {r.count} × {r.reason.replaceAll('_',' ')}</p>)}
          {(rematchDiagnosis.error_samples||[]).length>0&&<details className="space-y-2">
            <summary className="cursor-pointer font-bold">See exact products and existing comparisons ({rematchDiagnosis.error_samples.length} examples)</summary>
            {(rematchDiagnosis.error_samples||[]).map((sample,i)=><div key={i} className="rounded border p-2 my-2 space-y-1">
              <p className="font-semibold">{sample.source_title||'Saved observation #'+sample.observation_id}</p>
              <p className="text-red-800">Why unresolved: {(sample.reason_code||sample.message||'Unknown').replaceAll('_',' ')}</p>
              <p className="text-gray-600">Source {sample.source_id||'unknown'} · observation #{sample.observation_id} · {(sample.methods||[]).join(', ')}</p>
              {(sample.candidates||[]).map((candidate,j)=><div key={candidate.canonical_id||j} className="rounded bg-gray-50 p-2 space-y-1">
                <p>Candidate #{candidate.canonical_id} — {candidate.canonical_title||'No longer present'} ({candidate.identity_status||'unknown status'})</p>
                {candidate.exists&&<p>Current retailer offers: <strong>{candidate.offers}</strong> · historical listings: <strong>{candidate.legacy_listings}</strong> · accepted source links: <strong>{candidate.accepted_source_links}</strong></p>}
                <p>Similarity: {candidate.similarity??'unknown'} · missing: {(candidate.unknown||[]).join(', ')||'none'} · conflicts: {(candidate.conflicts||[]).join(', ')||'none'}</p>
              </div>)}
            </div>)}
          </details>}
        </div>}
        {rematchJob?.task_id&&<div className="rounded-lg border p-3 text-xs space-y-2" role="status">
          <p><strong>Rematch {rematchJob.status}</strong> ·
            {' '}{rematchJob.processed_items||rematchJob.metadata?.stats?.processed||0}/{rematchJob.total_items||rematchJob.selected||'?'} processed ·
            {' '}{rematchJob.metadata?.stats?.recovered_products||0} rematched ·
            {' '}{rematchJob.metadata?.stats?.failed_products||0} failed.</p>
          {rematchJob.metadata?.pre_rematch_backup&&<p className="break-all">Backup: {rematchJob.metadata.pre_rematch_backup}</p>}
          {rematchJob.error_message&&<p className="text-red-700">{rematchJob.error_message}</p>}
          {(rematchJob.metadata?.stats?.error_groups||[]).map((x,i)=><p key={i} className="text-red-700">{x.count} × {x.reason}</p>)}
          {['running','pending'].includes(rematchJob.status)&&<button type="button"
            disabled={rematchBusy} className="border border-red-300 rounded-lg px-3 py-2 text-red-700 font-bold"
            onClick={stopIdentityRematch}>Stop rematch after current product</button>}
          {['completed','completed_with_errors','failed'].includes(rematchJob.status)&&
            <button type="button" className="text-purple-700 underline" onClick={()=>refresh().catch(e=>setError(e.message))}>Refresh old review count</button>}
        </div>}
      </section>}
      {mode === 'reviews' && <section className="grid md:grid-cols-2 gap-4"><div className="bg-white border rounded-xl p-4"><div className="flex justify-between mb-3"><h3 className="font-semibold">{reviewView==='manual'?'Needs manual review':reviewView==='awaiting_gemini'?'Waiting for Gemini':reviewView==='evidence_needed'?'Awaiting stronger evidence':'All pending reviews'} ({total})</h3>
          <button type="button" className="text-xs font-semibold text-purple-700 underline" disabled={reviewsLoading} onClick={()=>refresh().catch(e=>setError(e.message))}>
            {reviewsLoading?'Loading...':'Refresh cases'}
          </button><select aria-label="Review type" value={kind} onChange={e => { setKind(e.target.value); setOffset(0); setSelected(null); }} className="border rounded p-1"><option value="">All types</option>{['identity', 'duplicate', 'category', 'formulation'].map(k => <option key={k}>{k}</option>)}</select></div>
        <div className="flex flex-wrap gap-2 mb-3 text-xs">
          {[['manual','Needs manual review'],['evidence_needed','Awaiting stronger evidence'],['awaiting_gemini','Waiting for Gemini'],['all','All unresolved cases']].map(([value,label])=>
            <button type="button" key={value} aria-pressed={reviewView===value} onClick={()=>{setReviewView(value);setOffset(0);setSelected(null)}}
              className={reviewView===value?'rounded-lg bg-purple-100 text-purple-900 px-2 py-1 font-semibold':'rounded-lg border px-2 py-1'}>{label}</button>)}
        </div>
        <div className="rounded-lg border p-3 space-y-2 mb-3 text-xs">
          <p className="font-bold">Separate weak suggestions from real review conflicts</p>
          <p className="text-gray-600">A similarity suggestion with no exact name, durable link, identifier or verified variant cannot establish a match. Keep its source observation and candidate for later evidence enrichment, rather than labeling it a mandatory manual decision.</p>
          <button type="button" className="rounded-lg border px-3 py-2 font-bold disabled:opacity-50"
            disabled={weakBusy} onClick={previewWeakSuggestions}>
            {weakBusy?'Checking…':'Preview weak-review triage — no changes'}
          </button>
          {weakPreview&&<div className="space-y-2">
            <p><strong>{weakPreview.eligible}</strong> safely identifiable weak suggestions among {weakPreview.total_pending} pending cases.
              Others remain in their existing review states.</p>
            {weakPreview.eligible>0&&<button type="button"
              disabled={weakBusy} className="rounded-lg border border-amber-500 px-3 py-2 font-bold text-amber-900 disabled:opacity-50"
              onClick={deferWeakSuggestions}>Move {weakPreview.eligible} weak suggestions to Awaiting stronger evidence</button>}
            <p className="text-gray-600">No canonical identities, source observations, offers or ingredient lists are changed.</p>
          </div>}
        </div>
        <label className="flex gap-2 items-center text-xs mb-2">
          <input type="checkbox" checked={reviews.length>0 && reviews.filter(x=>['identity','duplicate'].includes(x.kind)).every(x=>selectedReviewIds.includes(x.id))}
            onChange={e=>setSelectedReviewIds(e.target.checked?reviews.filter(x=>['identity','duplicate'].includes(x.kind)).map(x=>x.id):[])}/>
          Select identity cases visible on this page
        </label>
        <ul className="space-y-2 max-h-96 overflow-auto">{reviews.map(r => <li key={r.id} className="flex gap-2 items-start">
          <input type="checkbox" className="mt-4" disabled={!['identity','duplicate'].includes(r.kind)||['queued','processing'].includes(r.gemini_status)}
            checked={selectedReviewIds.includes(r.id)}
            onChange={e=>setSelectedReviewIds(prev=>e.target.checked?[...prev,r.id]:prev.filter(n=>n!==r.id))}/>
          <button className={`w-full text-left border rounded-lg p-3 ${selected?.id === r.id ? 'bg-purple-50 border-purple-500' : ''}`} onClick={() => { setSelected(r); setAction('request_evidence'); setCanonicalId(r.canonical_product_id || ''); setMergeSurvivor(r.details?.canonical_ids?.[0] || ''); }}><span className="block text-xs text-purple-700 uppercase">{r.kind} · {r.source_id || 'Canonical evidence'}</span><span className="block text-sm font-medium">{r.details.title || r.details.original_title || r.details.details?.titles?.join(' / ') || `Canonical ${r.canonical_product_id || 'unresolved'}`}</span>{r.gemini_status&&<span className="block text-xs text-gray-500 mt-1">Gemini: {r.gemini_status}{r.gemini_verdict?' · '+r.gemini_verdict:''}</span>}</button></li>)}</ul>
        {!reviews.length&&!reviewsError&&<p className="py-8 text-sm text-gray-500">
          {reviewsLoading?'Loading saved review cases...':
            reviewView==='manual'?'No cases currently require manual review. Check “All unresolved cases” or “Waiting for Gemini”.':
            reviewView==='evidence_needed'?'No deferred weak suggestions are waiting for stronger product evidence.':
            reviewView==='awaiting_gemini'?'No cases are waiting for Gemini. Select cases from “All unresolved cases” to enqueue them.':
            'No pending reviews in this view.'}
        </p>}<div className="flex justify-between text-sm mt-3"><button disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button><span>{Math.min(offset + reviews.length, total)} of {total}</span><button disabled={offset + 50 >= total || busy} onClick={() => setOffset(offset + 50)}>Next</button></div>
      </div><div className="bg-white border rounded-xl p-4">{selected ? <form className="space-y-3" onSubmit={e => { e.preventDefault(); perform(async () => { await api(`/reviews/${selected.id}`, { method: 'POST', body: JSON.stringify({ action, reviewer, reason, canonical_id: canonicalId ? Number(canonicalId) : null, category }) }); setMessage('Review saved. Re-ingest the observation to apply an accepted mapping.'); setSelected(null); setReason(''); }); }}>
        <h3 className="font-semibold">Review #{selected.id}</h3>
        {selected.gemini_explanation&&<p className="rounded-lg bg-purple-50 p-2 text-xs">
          Gemini: {selected.gemini_verdict} · confidence {selected.gemini_confidence} · {selected.gemini_explanation}
        </p>}
        {selected.kind==='identity'&&selected.current_comparators?.length>0&&
          <div className="rounded-lg border p-3 space-y-2 text-xs">
            <strong>What the system was comparing this product to</strong>
            {selected.current_comparators.map((candidate,i)=><div className="rounded border p-2 space-y-1" key={candidate.canonical_id||i}>
              <p>Suggested canonical #{candidate.canonical_id} ·
                {candidate.exists?' '+candidate.brand+' — '+candidate.title:' no longer exists in current database'}</p>
              {candidate.exists&&!candidate.comparator_available&&<p className="text-red-700 font-bold">
                No current retailer product listing supports this canonical. The old Gemini comparison contained no products.
              </p>}
              {!!candidate.listings?.length&&<>
                <p className="font-semibold">Actual current retailer listings:</p>
                {candidate.listings.map((offer,j)=><p key={j}>
                  {offer.retailer} · {offer.title}
                  {offer.product_url&&<> · <a className="underline text-purple-700" href={offer.product_url}
                    target="_blank" rel="noreferrer">Open listing</a></>}
                </p>)}
              </>}
              <p className="text-gray-600">Previous similarity suggestion: {candidate.similarity_suggestion??'none'} ·
                missing: {(candidate.unknown_attributes||[]).join(', ')||'none'}</p>
            </div>)}
          </div>}
        {selected.details.reason_code&&<p className="rounded-lg bg-amber-50 p-2 text-sm font-medium text-amber-900">Issue: {selected.details.reason_code.replaceAll('_',' ')}</p>}
        {selected.details.offers?.length>0&&<div className="text-xs text-gray-700 rounded-lg border p-2 space-y-1">
          <strong>Related store listings</strong>
          {selected.details.offers.slice(0,5).map(offer=><p key={offer.product_id}>#{offer.product_id} · {offer.store_id}: {offer.title}</p>)}
        </div>}
        {selected.kind==='duplicate'&&selected.details.canonical_ids?.length===2&&<div className="border rounded-lg p-3 space-y-2">
          <p className="text-xs font-semibold">Potential duplicate canonical products</p>
          <p className="text-xs text-gray-600">Inspect both products and their formulas before merging, or keep them separate using the review action below.</p>
          <select className={field} value={mergeSurvivor} onChange={e=>setMergeSurvivor(e.target.value)}>
            {selected.details.canonical_ids.map(cid=><option key={cid} value={cid}>Keep canonical #{cid}</option>)}
          </select>
          <button type="button" disabled={busy||!reviewer||!reason}
            className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-50" onClick={()=>perform(async()=>{
              if(!window.confirm('Merge the two canonical identities after reviewing their formulas and variants?'))return;
              const ids=selected.details.canonical_ids;
              const survivor=Number(mergeSurvivor), merged=ids.find(cid=>cid!==survivor);
              await api('/identity/merge',{method:'POST',body:JSON.stringify({survivor_id:survivor,merged_id:merged,reviewer,reason})});
              await api('/reviews/'+selected.id,{method:'POST',body:JSON.stringify({action:'dismiss',reviewer,reason:'Resolved by reviewed merge: '+reason})});
              setMessage('Canonical merge recorded with audit history.');
              setSelected(null);
            })}>Merge reviewed duplicates</button>
        </div>}{selected.details.source_url && <a className="text-purple-700 underline text-sm" target="_blank" rel="noreferrer" href={selected.details.source_url}>Open source declaration</a>}
        <details open><summary className="text-sm cursor-pointer">Identity and ingredient evidence</summary><pre className="text-xs whitespace-pre-wrap bg-gray-50 rounded p-3 overflow-auto max-h-64">{JSON.stringify(selected.details, null, 2)}</pre></details>
        <label className="block text-sm">Decision<select value={action} onChange={e => setAction(e.target.value)} className={field}><option value="request_evidence">Request further evidence</option><option value="dismiss">Dismiss finding</option><option value="keep_separate">Keep products separate</option>{canLink && <><option value="link">Accept canonical mapping</option><option value="cannot_link">Reject canonical mapping</option></>}<option value="category">Approve category</option></select></label>
        {['link', 'cannot_link'].includes(action) && <label className="block text-sm">Canonical ID<input required min="1" type="number" className={field} value={canonicalId} onChange={e => setCanonicalId(e.target.value)} /></label>}
        {action === 'category' && <label className="block text-sm">Category<select className={field} value={category} onChange={e => setCategory(e.target.value)}>{categories.map(c => <option key={c}>{c}</option>)}</select></label>}
        <label className="block text-sm">Reviewer<input required className={field} value={reviewer} onChange={e => setReviewer(e.target.value)} /></label><label className="block text-sm">Evidence and reason<textarea required rows="3" className={field} value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={busy} className="rounded-lg bg-purple-700 text-white px-4 py-2 disabled:opacity-50">Save review decision</button>
      </form> : <p className="text-gray-500 text-sm py-10">Select a finding to inspect source evidence and proposed actions.</p>}</div></section>}
    </main>
  </dialog>;
}
