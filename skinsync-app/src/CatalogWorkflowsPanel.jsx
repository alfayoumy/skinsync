import { useEffect, useState } from 'react';
import { apiFetch } from './apiFetch';

const API = 'http://127.0.0.1:8000';
const tabs = [
  ['products','1. Add & match products','New products, categories and duplicates'],
  ['offers','2. Update prices & stock','Existing offers only'],
  ['inci','3. Find ingredients','Existing products only'],
  ['llm','4. AI review queues','Jev and Gemini, run separately'],
];
const button = 'rounded-xl bg-purple-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50';
const input = 'w-full rounded-lg border border-gray-300 bg-white p-2 text-sm';
async function call(path, options) {
  const response=await apiFetch(API+path,options);
  const body=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || 'Operation failed.');
  return body;
}
const json=(body)=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const Metric=({label,value})=><div className="rounded-xl border bg-white p-3">
  <div className="text-xl font-black tabular-nums">{value ?? '—'}</div>
  <div className="text-xs text-gray-600">{label}</div>
</div>;

export default function CatalogWorkflowsPanel({onClose, initialTab='products',onOpenSources,onOpenReviews,onOpenIngredients,onOpenQueues,onOpenInciResearch}) {
  const [tab,setTab]=useState(initialTab);
  const [sources,setSources]=useState([]);
  const [sourceId,setSourceId]=useState('sourcebeauty');
  const [multiSource,setMultiSource]=useState(true);
  const [selectedSources,setSelectedSources]=useState([]);
  const [discoveryJob,setDiscoveryJob]=useState(null);
  const [jobStatus,setJobStatus]=useState(null);
  const [method,setMethod]=useState('shopify');
  const [startPage,setStartPage]=useState(1);
  const [pages,setPages]=useState(10);
  const [allPages,setAllPages]=useState(true);
  const [urls,setUrls]=useState('');
  const [preview,setPreview]=useState(true);
  const [price,setPrice]=useState(true);
  const [stock,setStock]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [result,setResult]=useState(null);
  const [eligibility,setEligibility]=useState(null);
  const [queues,setQueues]=useState(null);
  const [identityResult,setIdentityResult]=useState(null);
  const [identityBusy,setIdentityBusy]=useState(false);
  const [freshCert,setFreshCert]=useState(null);
  const [freshCertBusy,setFreshCertBusy]=useState(false);
  const [scope,setScope]=useState('unmatched_only');
  const [resetPreview,setResetPreview]=useState(null);
  const [resetConfirmation,setResetConfirmation]=useState('');
  const [resetBusy,setResetBusy]=useState(false);
  const [resetOutcome,setResetOutcome]=useState(null);
  const [selectivePreview,setSelectivePreview]=useState(null);
  const [selectiveConfirmation,setSelectiveConfirmation]=useState('');
  const [selectiveJob,setSelectiveJob]=useState(null);
  const [selectiveStatus,setSelectiveStatus]=useState(null);
  const [selectiveBusy,setSelectiveBusy]=useState(false);
  const [stalePreview,setStalePreview]=useState(null);
  const [staleConfirmation,setStaleConfirmation]=useState('');
  const [staleWorkersChecked,setStaleWorkersChecked]=useState(false);
  const [staleBusy,setStaleBusy]=useState(false);
  const [staleOutcome,setStaleOutcome]=useState(null);
  const source=sources.find(s=>s.id===sourceId);
  useEffect(()=>{
    let active=true;
    call('/admin/aggregation/sources').then(x=>{if(active){
      setSources(x.sources || []);
      const enabled=(x.sources || []).filter(z=>z.enabled).map(z=>z.id);
      setSelectedSources(enabled);
      if(enabled.length)setSourceId(enabled[0]);
    }})
      .catch(e=>{if(active)setError(e.message)});
    return ()=>{active=false};
  },[]);
  useEffect(()=>{setError('');setResult(null)},[tab,sourceId,method]);
  useEffect(()=>{
    let active=true;
    if(tab==='inci')call('/admin/waterfall/batch/eligibility?scope='+scope)
      .then(x=>{if(active)setEligibility(x)}).catch(e=>{if(active)setError(e.message)});
    if(tab==='llm')call('/admin/queues/status')
      .then(x=>{if(active)setQueues(x)}).catch(e=>{if(active)setError(e.message)});
    return ()=>{active=false};
  },[tab,scope]);
  useEffect(()=>{
    if(!discoveryJob)return;
    let active=true;
    const check=()=>call('/admin/workflows/products/discover-multiple/'+encodeURIComponent(discoveryJob))
      .then(status=>{
        if(!active)return;
        setJobStatus(status);
        if(!['running','pending'].includes(status.status)){
          setResult({workflow:'multi_source_discovery',dry_run:preview,sources:status.metadata?.sources||[],
            summary:status.metadata?.summary||{},task_status:status.status});
          setDiscoveryJob(null);
        }
      }).catch(e=>{if(active){setError(e.message);setDiscoveryJob(null);}});
    check();
    const id=setInterval(check,1700);
    return ()=>{active=false;clearInterval(id);};
  },[discoveryJob]);
  async function prepareAndOpenReviews() {
    setIdentityBusy(true);setError('');
    try {
      const report=await call('/admin/workflows/products/prepare-reviews',json({}));
      setIdentityResult(report);
      if(!dryRun && report.auto_verified>0){
        // The server scans all legacy identities, so the remaining count
        // is genuinely lower after a committed application.
        setEligibility(null);
      }
      onOpenReviews();
    } catch(e){setError(e.message)} finally {setIdentityBusy(false);}
  }
  async function cancelScan() {
    if(!discoveryJob)return;
    try {
      await call('/admin/workflows/products/discover-multiple/'+encodeURIComponent(discoveryJob)+'/cancel',
        {method:'POST'});
    } catch(e){setError(e.message);}
  }
  async function checkExisting(dryRun) {
    if(!dryRun&&!window.confirm('Automatically verify only legacy products with matching brand, product identity and variants, with no conflicts or duplicate canonical identity? No prices or ingredients will change.'))return;
    setIdentityBusy(true);setError('');
    try {
      const report=await call('/admin/workflows/products/verify-existing',json({
        dry_run:dryRun,max_verified:500
      }));
      setIdentityResult(report);
    } catch(e){setError(e.message)} finally {setIdentityBusy(false)}
  }
  async function inspectFreshIdentity(apply=false) {
    if(apply&&!window.confirm('Certify only exact independently agreeing brand, product and formulation variants from at least two different retailers? Single-source and conflicting items will remain unverified.'))return;
    setFreshCertBusy(true);setError('');
    try{
      const plan=await call('/admin/waterfall/identity-certification',json({apply,limit:200}));
      setFreshCert(plan);
      if(apply)setEligibility(null);
    }catch(e){setError(e.message)}
    finally{setFreshCertBusy(false)}
  }
  async function previewReset() {
    setResetBusy(true);setError('');setResetOutcome(null);setResetConfirmation('');
    try { setResetPreview(await call('/admin/workflows/catalog/reset/preview')); }
    catch(e) {setError(e.message);setResetPreview(null);}
    finally {setResetBusy(false);}
  }
  async function executeReset() {
    if(!resetPreview?.can_reset||resetConfirmation!=='RESET ALL PRODUCT DATA')return;
    setResetBusy(true);setError('');
    try {
      const response=await call('/admin/workflows/catalog/reset',json({
        fingerprint:resetPreview.fingerprint,confirmation:resetConfirmation
      }));
      setResetOutcome(response);setResetPreview(null);setResetConfirmation('');
      setEligibility(null);
    } catch(e){setError(e.message);setResetPreview(null)}
    finally {setResetBusy(false)}
  }
  async function previewSelective() {
    setSelectiveBusy(true);setError('');setSelectiveConfirmation('');setSelectiveStatus(null);
    try {setSelectivePreview(await call('/admin/workflows/catalog/selective-rebuild/preview'));}
    catch(e){setError(e.message);setSelectivePreview(null);}
    finally{setSelectiveBusy(false);}
  }
  async function previewStaleTasks() {
    setStaleBusy(true);setError('');setStalePreview(null);
    setStaleOutcome(null);setStaleConfirmation('');setStaleWorkersChecked(false);
    try {setStalePreview(await call('/admin/workflows/catalog/stale-tasks/preview'))}
    catch(e){setError(e.message)}
    finally{setStaleBusy(false)}
  }
  async function reconcileStaleTasks() {
    if(!stalePreview?.can_reconcile||!staleWorkersChecked
       ||staleConfirmation!=='RECONCILE STALE TASK RECORDS')return;
    setStaleBusy(true);setError('');
    try {
      const data=await call('/admin/workflows/catalog/stale-tasks/reconcile',json({
        fingerprint:stalePreview.fingerprint,
        confirmation:staleConfirmation,workers_checked:true
      }));
      setStaleOutcome(data);setStalePreview(null);setStaleConfirmation('');
      setStaleWorkersChecked(false);
      // Immediately rerun the rebuild preflight from unchanged observations.
      await previewSelective();
    }catch(e){setError(e.message);setStalePreview(null)}
    finally{setStaleBusy(false)}
  }
  async function startSelective() {
    if(!selectivePreview?.can_rebuild||selectiveConfirmation!=='REBUILD FROM SAVED DISCOVERY')return;
    setSelectiveBusy(true);setError('');
    try {
      const job=await call('/admin/workflows/catalog/selective-rebuild',json({
        fingerprint:selectivePreview.fingerprint,
        discovery_task_id:selectivePreview.discovery_task_id,
        confirmation:selectiveConfirmation
      }));
      setSelectiveJob(job.job_id);setSelectivePreview(null);setSelectiveConfirmation('');
    }catch(e){setError(e.message);setSelectivePreview(null)}
    finally{setSelectiveBusy(false);}
  }
  useEffect(()=>{
    if(!selectiveJob)return;
    let active=true;
    const poll=()=>call('/admin/workflows/catalog/selective-rebuild/jobs/'+encodeURIComponent(selectiveJob))
      .then(data=>{
        if(!active)return;
        setSelectiveStatus(data);
        if(['completed','failed'].includes(data.status)){
          setSelectiveJob(null);setEligibility(null);
        }
      }).catch(e=>{if(active)setError(e.message)});
    poll();
    const timer=setInterval(poll,2000);
    return ()=>{active=false;clearInterval(timer)};
  },[selectiveJob]);
  async function run() {
    setBusy(true);setError('');setResult(null);
    try {
      let response;
      if(tab==='products' && multiSource) {
        if(!selectedSources.length)throw new Error('Select at least one enabled source.');
        const urlsBySource={};
        for(const line of urls.split('\n').map(x=>x.trim()).filter(Boolean)){
          const parts=line.split('|');
          if(parts.length!==2)throw new Error('For multiple sources, enter URLs as source_id | https://product-url');
          const id=parts[0].trim(),url=parts[1].trim();
          if(!selectedSources.includes(id))throw new Error('The source '+id+' must be selected first.');
          (urlsBySource[id]??=[]).push(url);
        }
        response=await call('/admin/workflows/products/discover-multiple',json({
          source_ids:selectedSources,start_page:Number(startPage),pages:allPages?0:Number(pages),
          dry_run:preview,urls_by_source:urlsBySource
        }));
        setJobStatus(null);setDiscoveryJob(response.task_id);return;
      } else if(tab==='products' && method==='shopify') {
        response=await call('/admin/workflows/products/discover-multiple',json({
          source_ids:[sourceId],start_page:Number(startPage),
          pages:allPages?0:Number(pages),dry_run:preview
        }));
        setJobStatus(null);setDiscoveryJob(response.task_id);return;
      } else if(tab==='products') {
        const list=urls.split('\n').map(s=>s.trim()).filter(Boolean);
        if(!list.length || list.length>25)throw new Error('Enter 1–25 URLs, one per line.');
        response=await call('/admin/aggregation/sources/'+encodeURIComponent(sourceId)+'/collect',json({
          urls:list,max_products:list.length,dry_run:preview
        }));
        const outcomes=(response.results||[]).flatMap(row=>row.results||[]);
        const count=(status)=>outcomes.filter(row=>row.status===status).length;
        response={...response,workflow:'product_discovery',fetched:response.observations,
          existing_products_matched:count('matched'),new_products_staged:count('provisional'),
          needs_review:count('review'),note:'Exact product matches link automatically; new identities stay provisional until approved.'};
      } else if(tab==='offers') {
        response=await call('/admin/workflows/offers/refresh',json({
          source_id:sourceId,start_page:Number(startPage),pages:Number(pages),
          update_price:price,update_stock:stock,dry_run:preview
        }));
      }
      setResult(response);
    } catch(e){setError(e.message)} finally {setBusy(false)}
  }
  return <div className="fixed inset-0 z-50 bg-black/60 p-3 sm:p-6 flex justify-center items-center">
    <section role="dialog" aria-modal="true" aria-label="Catalog operations"
      className="w-full max-w-6xl max-h-[94vh] bg-white shadow-2xl rounded-3xl flex flex-col overflow-hidden">
      <header className="p-5 border-b flex justify-between gap-4">
        <div><h2 className="text-xl font-black">Catalog operations</h2>
          <p className="text-sm text-gray-600">Four separate jobs. Running one does not automatically run the others.</p></div>
        <button type="button" onClick={onClose} className="rounded-xl bg-gray-100 px-4 py-2 text-sm font-bold">Close</button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-gray-50 p-5 space-y-4">
        <nav aria-label="Catalog workflows" className="grid sm:grid-cols-2 lg:grid-cols-4 gap-2">
          {tabs.map(([id,name,detail])=><button type="button" key={id} onClick={()=>setTab(id)}
            aria-current={tab===id?'step':undefined}
            className={tab===id?'rounded-xl border border-purple-500 bg-purple-50 p-3 text-left':'rounded-xl border border-gray-200 bg-white p-3 text-left'}>
            <strong className="block text-sm">{name}</strong><span className="text-xs text-gray-500">{detail}</span>
          </button>)}
        </nav>
        {error&&<p role="alert" className="rounded-lg bg-red-50 border border-red-200 text-red-800 p-3 text-sm">{error}</p>}
        {(tab==='products'||tab==='offers')&&<div className="max-w-3xl mx-auto space-y-4">
          <section className="rounded-xl border bg-white p-5 space-y-4">
            <h3 className="text-lg font-bold">{tab==='products'?'Discover & match products':'Update existing store offers'}</h3>
            <p className="text-sm text-gray-600">{tab==='products'
              ?'Confident matches link automatically. New products are staged and unclear matches go to review. This does not launch INCI or AI queues.'
              :'Prices and availability are updated only for already-linked offers. Unknown products and variants are skipped. No identity or ingredient changes.'}</p>
            {tab==='products'&&<div className="rounded-xl border border-purple-200 bg-purple-50 p-3 space-y-2">
              <label className="flex items-center gap-2 text-sm font-bold"><input type="checkbox"
                checked={multiSource} onChange={e=>setMultiSource(e.target.checked)}/>
                Discover from multiple collection sources at once
              </label>
              {multiSource&&<>
                <div className="flex items-center gap-3 text-xs">
                  <button type="button" className="underline text-purple-800 font-bold"
                    onClick={()=>setSelectedSources(sources.filter(x=>x.enabled).map(x=>x.id))}>Select all collection-enabled sources</button>
                  <button type="button" className="underline" onClick={()=>setSelectedSources([])}>Clear</button>
                  <span>{selectedSources.length} selected</span>
                </div>
                <div className="max-h-40 overflow-auto grid sm:grid-cols-2 gap-1 bg-white rounded-lg p-2">
                  {sources.filter(x=>x.enabled).map(src=><label key={src.id} className="flex gap-2 items-center text-xs py-1">
                    <input type="checkbox" checked={selectedSources.includes(src.id)}
                      onChange={e=>setSelectedSources(prev=>e.target.checked?[...prev,src.id]:prev.filter(x=>x!==src.id))}/>
                    <span>{src.name} ({src.adapter_type})</span>
                  </label>)}
                </div>
                <p className="text-xs text-gray-600">Shopify sources scan the selected catalog pages. Other websites use supplied or previously observed product URLs. Sources without a catalog feed or known URLs will be reported as needing URLs, not counted as successfully crawled.</p>
                <label className="block text-xs font-semibold">Optional product URLs for non-Shopify sources
                  <textarea className={input} rows={3} value={urls} onChange={e=>setUrls(e.target.value)}
                    placeholder="zahra | https://zahraegypt.com/products/example"/>
                </label>
              </>}
            </div>}
            {(!multiSource||tab==='offers')&&<>
              <label className="block text-sm font-semibold">Source</label>
              <select className={input} value={sourceId} onChange={e=>setSourceId(e.target.value)}>
                {sources.map(src=><option key={src.id} value={src.id}>{src.name} · {src.enabled?'Enabled':'Paused'} · {src.adapter_type}</option>)}
              </select>
              {source&&!source.enabled&&<p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-950">
                Collection is paused. <button type="button" className="underline font-semibold" onClick={onOpenSources}>Open source settings</button>
              </p>}
            </>}
            {tab==='products'&&!multiSource&&<div className="flex gap-4 text-sm">
              <label><input type="radio" checked={method==='shopify'} onChange={()=>setMethod('shopify')}/> Shopify pages</label>
              <label><input type="radio" checked={method==='urls'} onChange={()=>setMethod('urls')}/> Specific product URLs</label>
            </div>}
            {(tab==='offers'||multiSource||method==='shopify')?<div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-semibold">Starting page<input className={input} type="number" min="1" value={startPage} onChange={e=>setStartPage(e.target.value)}/></label>
              <div className="text-xs font-semibold space-y-2">
                {tab==='products'&&<label className="flex items-center gap-2 text-sm font-semibold">
                  <input type="checkbox" checked={allPages} onChange={e=>setAllPages(e.target.checked)} />
                  All pages — stop when Shopify runs out of products
                </label>}
                {(!allPages||tab==='offers')&&<label className="block">Number of pages to scan
                  <input className={input} type="number" min="1" value={pages} onChange={e=>setPages(e.target.value)} />
                </label>}
                {tab==='products'&&allPages&&<p className="font-normal text-gray-500">Tracked scan · per-page progress · pause with Stop scan · respects the source rate limit</p>}
              </div>
            </div>:<label className="block text-sm font-semibold">Product URLs, one per line (max 25)
              <textarea className={input} rows={5} value={urls} onChange={e=>setUrls(e.target.value)}/>
            </label>}
            {tab==='offers'&&<div className="flex gap-4 text-sm">
              <label><input type="checkbox" checked={price} onChange={e=>setPrice(e.target.checked)}/> Price</label>
              <label><input type="checkbox" checked={stock} onChange={e=>setStock(e.target.checked)}/> Availability</label>
            </div>}
            <div className="flex items-center justify-between flex-wrap gap-3">
              <label className="text-sm"><input type="checkbox" checked={preview} onChange={e=>setPreview(e.target.checked)}/> Preview only — save nothing</label>
              <button type="button" className={button} onClick={run}
                disabled={busy||Boolean(discoveryJob)||(tab==='products'&&multiSource
                  ? selectedSources.length===0
                  : !source?.enabled||((tab==='offers'||method==='shopify')&&source?.adapter_type!=='shopify'))
                  ||(tab==='offers'&&(!price&&!stock||source?.source_type!=='retailer'))}>
                {busy?'Checking source...':preview?'Preview changes':tab==='products'?'Import and match':'Update existing offers'}
              </button>
            </div>
            {!multiSource&&source?.adapter_type!=='shopify'&&(tab==='offers'||method==='shopify')&&<p className="text-xs text-amber-800">
              This source is not Shopify. Product pages can be imported using Specific product URLs; automated offer refresh is not supported yet.
            </p>}
          </section>
          {jobStatus&&<section className="rounded-xl border bg-white p-4 space-y-2" role="status">
            <h4 className="font-bold">Product discovery: {jobStatus.status}</h4>
            <p className="text-xs text-gray-600">{jobStatus.processed_items||0} / {jobStatus.total_items||selectedSources.length} sources finished</p>
            {jobStatus.metadata?.active_source&&<p className="text-xs text-purple-800">
              Scanning {jobStatus.metadata.active_source}, page {jobStatus.metadata.active_page};
              {jobStatus.metadata.active_products_found} products found in this source so far
            </p>}
            {discoveryJob&&<button type="button" className="rounded-lg border border-red-300 px-3 py-2 text-xs font-semibold text-red-700" onClick={cancelScan}>
              Stop scan after the current page
            </button>}
            {(jobStatus.metadata?.sources||[]).map(entry=><p key={entry.source_id} className="text-xs">
              {entry.name||entry.source_id}: {entry.status} {entry.reason||''}
            </p>)}
          </section>}
          {result&&<section role="status" className="rounded-xl border bg-white p-5 space-y-3">
            <h4 className="font-bold">{result.dry_run?'Preview — nothing saved':'Operation completed'}</h4>
            {result.workflow==='multi_source_discovery'&&<div className="space-y-1">
              {result.sources.map(entry=><div key={entry.source_id} className="rounded-lg border bg-gray-50 p-2 text-xs">
                <strong>{entry.name||entry.source_id}</strong> — {entry.status}
                <span className="ml-2">{entry.fetched??0} found · {entry.existing_products_matched??0} linked · {entry.new_products_staged??0} staged</span>
                {entry.pages_scanned!==undefined&&<p className="text-gray-600 mt-1">
                  {entry.pages_scanned} pages · last page {entry.last_completed_page??'—'} ·
                  {entry.catalog_exhausted?'Reached catalog end':entry.stop_reason==='page_limit'?'Stopped at your page limit':'Scan stopped: '+entry.stop_reason}
                  {entry.last_completed_page&&<span> · resume at page {entry.next_page}</span>}
                </p>}
                {entry.reason&&<p className="text-amber-800 mt-1">{entry.reason}</p>}
                {entry.coverage&&<p className="text-gray-600 mt-1">{entry.coverage}</p>}
              </div>)}
            </div>}
            {result.workflow!=='multi_source_discovery'&&<div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {tab==='offers'?<>
                <Metric label="Offers updated" value={result.offers_updated}/><Metric label="Already current" value={result.unchanged_offers}/>
                <Metric label="Unknown variants skipped" value={result.unknown_variants_skipped}/>
                <Metric label="Different identities skipped" value={result.identity_mismatches_skipped}/>
              </>:<>
                <Metric label="Products found" value={result.fetched??result.observations}/>
                <Metric label="Matched to existing" value={result.existing_products_matched??'—'}/>
                <Metric label="New products staged" value={result.new_products_staged??'—'}/>
                <Metric label="Needs review" value={result.needs_review??'—'}/>
              </>}
            </div>}
            <p className="text-sm text-gray-600">{result.note || 'Review source observations and products that need a decision.'}</p>
          </section>}
          {tab==='products'&&<section className="rounded-xl border border-purple-200 bg-white p-5 space-y-3">
            <h3 className="font-bold">Verify fresh identities using independent retailers</h3>
            <p className="text-sm text-gray-600">After a selective rebuild, new canonicals are <strong>provisional</strong>, so the INCI waterfall cannot run on them yet. Confirming a product's exact matching brand, identity and formulation-defining variant across at least two independent stores can verify its identity without Gemini. Single-source, ambiguous, duplicate or conflicting products are excluded.</p>
            <div className="flex gap-2 flex-wrap">
              <button type="button" className="rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50"
                disabled={freshCertBusy} onClick={()=>inspectFreshIdentity(false)}>
                Preview independent exact matches
              </button>
              {freshCert?.certifiable>0&&<button type="button" className={button}
                disabled={freshCertBusy} onClick={()=>inspectFreshIdentity(true)}>
                Certify next {Math.min(200,freshCert.certifiable)} verified cross-store identities
              </button>}
            </div>
            {freshCert&&<p className="text-xs text-gray-600" role="status">
              {freshCert.dry_run?freshCert.certifiable+' certifiable products':freshCert.processed+' identity certifications saved'} · No INCI, Gemini decisions or prices modified.
            </p>}
          </section>}
          {tab==='products'&&<section className="rounded-xl border bg-white p-5 space-y-3">
            <h3 className="font-bold text-gray-900">Check existing products automatically (legacy)</h3>
            <p className="text-sm text-gray-600">A product can be verified without a person when its brand and product identity match, its variants are compatible, and no competing product or ingredient conflict exists. Uncertain records stay unchanged for review.</p>
            <div className="flex flex-wrap gap-3">
              <button type="button" disabled={identityBusy} className="rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50" onClick={()=>checkExisting(true)}>
                Find safe exact matches
              </button>
              {identityResult?.safe_exact_matches_found>0&&
                <button type="button" disabled={identityBusy} className={button} onClick={()=>checkExisting(false)}>
                  Apply next {Math.min(500,identityResult.safe_exact_matches_found)} safe matches
                </button>}
            </div>
            {identityResult&&<div role="status" className="rounded-lg border border-purple-100 bg-purple-50 p-3 text-sm space-y-2">
              <p className="font-semibold">{identityResult.dry_run
                ? identityResult.safe_exact_matches_found+' safe legacy matches available — preview only'
                : identityResult.auto_verified+' product identities verified and saved'}</p>
              <p className="text-xs font-medium">{identityResult.remaining_safe_matches} safe matches still eligible for the next batch · {identityResult.pending_review_cases} pending review cases</p>
              <p className="text-xs text-gray-700">{identityResult.note}</p>
              <details><summary className="cursor-pointer text-xs font-semibold">Why other records were not matched automatically</summary>
                <ul className="text-xs mt-2 space-y-1">{Object.entries(identityResult.reasons_not_auto_matched||{}).map(([name,value])=><li key={name}>{name.replaceAll('_',' ')}: {value}</li>)}</ul>
              </details>
            </div>}
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={identityBusy} className="rounded-lg border px-4 py-2 text-sm font-bold disabled:opacity-50" onClick={prepareAndOpenReviews}>
                Prepare review cases & open Product Reviews
              </button>
              <button type="button" className="rounded-lg border px-4 py-2 text-sm font-bold" onClick={onOpenSources}>
                Manage product sources
              </button>
            </div>
          </section>}
          {tab==='products'&&<section className="rounded-xl border border-green-200 bg-white p-5 space-y-3">
            <h3 className="font-bold text-gray-900">Selective rebuild from saved discovery</h3>
            <p className="text-sm text-gray-600">Recommended after your six-source discovery completes. Archives the database, purges historical product identities, old ingredient approvals and Gemini decisions, and rebuilds products, variants and prices from saved Shopify source observations <strong>without scraping again</strong>.</p>
            <p className="text-xs text-gray-600">Requires a completed full-catalog discovery saved with Preview only turned off. Source observations survive; old saved Gemini decisions remain in the backup.</p>
            <button type="button" className="rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50"
              disabled={selectiveBusy||Boolean(selectiveJob)||Boolean(discoveryJob)} onClick={previewSelective}>
              {selectiveBusy?'Checking...':'Preview selective rebuild'}
            </button>
            {selectivePreview&&<div role="status" className="border rounded-lg p-3 space-y-2 text-sm">
              <p className="font-semibold">{selectivePreview.observations_to_replay} saved products from {selectivePreview.source_ids.length} sources can be replayed without scraping.</p>
              <p className="text-xs">Purge {selectivePreview.legacy_products_to_purge} legacy listings, {selectivePreview.legacy_canonicals_to_purge} old canonicals, {selectivePreview.old_inci_evidence_to_purge} old INCI records and archive {selectivePreview.old_gemini_decisions_to_archive} Gemini decisions.</p>
              <div className="grid sm:grid-cols-2 gap-1 text-xs">{Object.entries(selectivePreview.sources||{}).map(([sid,data])=>
                <div key={sid}>{sid}: {data.saved_product_observations} saved / {data.fetched_in_run} scanned</div>)}</div>
              {!selectivePreview.can_rebuild&&<div className="text-red-700 space-y-2">
                {selectivePreview.errors.map((item,i)=><p key={i}>{item}</p>)}
                {selectivePreview.active_jobs?.length>0&&<div className="rounded-lg border border-red-200 p-3 text-xs">
                  <p className="font-bold">Blocking task records (not necessarily live processes)</p>
                  {selectivePreview.active_jobs.map(job=><div key={job.task_id} className="mt-1 break-all">
                    <strong>{job.task_type}</strong> — {job.status} · {job.current_stage||'no stage'} · updated {job.updated_at||'unknown'} · <code>{job.task_id}</code>
                  </div>)}
                  <p className="mt-2">Check these task IDs and verify no scanner or worker is running before changing a stuck task's status. This rebuild does not dismiss running task records automatically.</p>
                </div>}
                {selectivePreview.active_workers?.length>0&&<p>Active workers: {selectivePreview.active_workers.join(', ')}</p>}
              </div>}
              {selectivePreview.pending_tasks_not_running>0&&<p className="text-xs text-gray-500">{selectivePreview.pending_tasks_not_running} pending task records are not running and do not block a rebuild.</p>}
              {!selectivePreview.can_rebuild&&selectivePreview.active_jobs?.length>0&&
                <button type="button" className="rounded-lg border border-amber-400 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-900 disabled:opacity-50"
                  disabled={staleBusy||selectiveBusy} onClick={previewStaleTasks}>
                  Inspect potentially orphaned task records
                </button>}
              {selectivePreview.can_rebuild&&<div className="space-y-2">
                <label className="block text-sm font-semibold">Type REBUILD FROM SAVED DISCOVERY to proceed.
                  <input className={input} autoComplete="off" value={selectiveConfirmation}
                    onChange={e=>setSelectiveConfirmation(e.target.value)}/>
                </label>
                <button type="button" className="rounded-lg bg-gray-900 px-4 py-2 text-white font-bold disabled:opacity-50"
                  disabled={selectiveBusy||selectiveConfirmation!=='REBUILD FROM SAVED DISCOVERY'} onClick={startSelective}>
                  Back up and rebuild without scraping
                </button>
              </div>}
            </div>}
            {staleOutcome&&<p className="rounded-lg border p-3 text-sm" role="status">
              {staleOutcome.reconciled_count} stale task records marked failed and audit-logged. No source observations or products were changed.
            </p>}
            {stalePreview&&<div className="rounded-xl border border-amber-300 p-4 space-y-3 text-sm">
              <h4 className="font-bold">Reconcile stale task records</h4>
              <p className="text-xs text-gray-600">These task rows are at least {stalePreview.minimum_age_hours} hours old and match strict terminal-stage or test-task patterns. This is a database status repair only, not a worker cancellation or product reset.</p>
              <p className="text-xs font-semibold">{stalePreview.candidates.length} eligible records · {stalePreview.ineligible_running.length} other running records remain protected</p>
              {stalePreview.candidates.map(job=><p className="break-all text-xs" key={job.task_id}>
                <strong>{job.task_type}</strong> · {job.age_hours}h old · {job.reason} · <code>{job.task_id}</code>
              </p>)}
              {stalePreview.ineligible_running.length>0&&<details className="text-xs">
                <summary className="cursor-pointer">Other running tasks (not eligible for automatic reconciliation)</summary>
                {stalePreview.ineligible_running.map(job=><p className="break-all" key={job.task_id}>
                  {job.task_type} · {job.age_hours??'?'}h · <code>{job.task_id}</code>
                </p>)}
              </details>}
              {stalePreview.errors.map((e,i)=><p className="text-red-700 text-xs" key={i}>{e}</p>)}
              {stalePreview.can_reconcile&&<>
                <label className="flex gap-2 text-xs items-start">
                  <input type="checkbox" checked={staleWorkersChecked} onChange={e=>setStaleWorkersChecked(e.target.checked)}/>
                  I checked the running backend processes and confirmed that none of these task IDs is actually executing.
                </label>
                <label className="block text-xs font-semibold">Type RECONCILE STALE TASK RECORDS
                  <input className={input} value={staleConfirmation} autoComplete="off"
                    onChange={e=>setStaleConfirmation(e.target.value)}/>
                </label>
                <button type="button" className="rounded-lg border border-amber-400 px-3 py-2 font-semibold text-amber-900 disabled:opacity-50"
                  disabled={staleBusy||!staleWorkersChecked||staleConfirmation!=='RECONCILE STALE TASK RECORDS'}
                  onClick={reconcileStaleTasks}>
                  Mark {stalePreview.candidates.length} orphaned records failed and recheck rebuild
                </button>
              </>}
            </div>}
                        {selectiveStatus&&<div role="status" className="border rounded-lg p-3 text-sm">
              <p className="font-semibold">Selective rebuild: {selectiveStatus.status}</p>
              {selectiveStatus.progress&&<p className="text-xs">{selectiveStatus.progress.processed} / {selectiveStatus.progress.total} observations replayed</p>}
              {selectiveStatus.error_message&&<p className="text-red-700">{selectiveStatus.error_message} — The catalog rebuild was rolled back.</p>}
              {selectiveStatus.result&&<p>{selectiveStatus.result.canonicals} canonicals and {selectiveStatus.result.offers} offers rebuilt from {selectiveStatus.result.replayed} saved observations.</p>}
              {selectiveStatus.backup_path&&<p className="text-xs break-all">Full database backup: {selectiveStatus.backup_path}</p>}
            </div>}
          </section>}
          {tab==='products'&&<section className="rounded-xl border border-red-200 bg-white p-5 space-y-3">
            <h3 className="font-bold text-red-900">Reset catalog data</h3>
            <p className="text-sm text-gray-600">Archive the entire SQLite database, then remove product listings, canonical identities, offers, ingredient evidence and reviews. Preserve reference ingredients, taxonomies, source settings and API quota history. Saved Gemini decisions are retained in the backup only.</p>
            <button type="button" className="rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50" disabled={resetBusy||Boolean(discoveryJob)} onClick={previewReset}>
              {resetBusy?'Checking...':'Preview catalog reset'}
            </button>
            {resetPreview&&<div role="status" className="border rounded-lg p-3 space-y-2 text-sm">
              <p className="font-semibold">{resetPreview.records_to_delete} records across {Object.keys(resetPreview.tables||{}).length} catalog tables would be deleted.</p>
              {resetPreview.active_jobs?.length>0&&<p className="text-red-700">{resetPreview.active_jobs.length} active or not-yet-stopped jobs block reset.</p>}
              {resetPreview.active_workers?.length>0&&<p className="text-red-700">Active workers: {resetPreview.active_workers.join(', ')}</p>}
              <details><summary className="cursor-pointer font-semibold">View affected and preserved tables</summary>
                <div className="mt-2 grid sm:grid-cols-2 gap-2 text-xs">
                  <div><strong>Delete</strong>{Object.entries(resetPreview.tables||{}).map(([k,v])=><div key={k}>{k}: {v.rows}</div>)}</div>
                  <div><strong>Preserve</strong>{Object.entries(resetPreview.preserved_tables||{}).map(([k,v])=><div key={k}>{k}: {v}</div>)}</div>
                </div>
              </details>
              {resetPreview.can_reset&&<div className="space-y-2">
                <label className="block font-semibold">Type RESET ALL PRODUCT DATA to authorize this irreversible live reset.
                  <input className={input} autoComplete="off" value={resetConfirmation}
                    onChange={e=>setResetConfirmation(e.target.value)} />
                </label>
                <button type="button" className="rounded-lg bg-red-700 px-4 py-2 text-white font-bold disabled:opacity-50"
                  disabled={resetBusy||resetConfirmation!=='RESET ALL PRODUCT DATA'} onClick={executeReset}>
                  Back up database and reset catalog
                </button>
              </div>}
              {!resetPreview.can_reset&&<p className="text-red-700">Finish or cancel active jobs and allow their workers to exit before retrying.</p>}
            </div>}
            {resetOutcome&&<div role="status" className="border rounded-lg p-3 text-sm">
              <strong>Catalog reset completed.</strong> {resetOutcome.records_deleted} records cleared.
              <p className="break-all text-xs">Verified backup: {resetOutcome.backup_path}</p>
            </div>}
          </section>}
        </div>}
        {tab==='inci'&&<section className="max-w-3xl mx-auto rounded-xl border bg-white p-5 space-y-4">
          <h3 className="text-lg font-bold">Find ingredients for existing products</h3>
          <p className="text-sm text-gray-600">This searches for INCI evidence; it does not add new products or update stock and prices. Exact matching is automatic when evidence is sufficient. Incomplete or conflicting results remain unapproved.</p>
          <label className="text-sm font-semibold block">Products to check</label>
          <select className={input} value={scope} onChange={e=>setScope(e.target.value)}>
            <option value="unmatched_only">Products needing ingredient lists</option>
            <option value="all_skincare">Recheck all identity-approved skincare products</option>
          </select>
          {eligibility&&<div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Metric label="Existing skincare products" value={eligibility.total_skincare}/>
            <Metric label="Selected scope" value={eligibility.in_scope}/>
            <Metric label="Ready for ingredient search" value={eligibility.eligible_before_limit}/>
            <Metric label="Need product identity checks" value={eligibility.blocked_unverified}/>
          </div>}
          <p className="text-xs text-gray-500">Needing identity checks does not mean a manual review is always required. Product matching is handled by the first workflow.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={button} onClick={onOpenIngredients}>Open ingredient lookup</button>
            <button type="button" className="rounded-xl border px-4 py-2 text-sm font-bold" onClick={onOpenInciResearch}>INCI research queue · MCP + Gemini</button>
          </div>
        </section>}
        {tab==='llm'&&<section className="max-w-3xl mx-auto rounded-xl border bg-white p-5 space-y-4">
          <h3 className="text-lg font-bold">AI review queues</h3>
          <div className="rounded-lg border border-purple-200 bg-purple-50 p-3 space-y-2">
            <strong className="text-sm">Gemini for product identity reviews</strong>
            <p className="text-xs text-gray-700">Compare retailer names against canonical products in batches, auto-approve guarded high-confidence matches and send difficult cases to Product Reviews.</p>
            <button type="button" className={button} onClick={onOpenReviews}>Open Gemini product matching</button>
          </div>
          <p className="text-sm text-gray-600">Run Jev or Gemini separately on queued ambiguous matches. This does not crawl retailers, create products or update prices. A model's approval alone does not verify an exact INCI source.</p>
          <div className="grid grid-cols-3 gap-2">
            <Metric label="Jev waiting" value={queues?.jev?.queue_depth?.pending}/>
            <Metric label="Gemini waiting" value={queues?.gemini?.queue_depth?.pending}/>
            <Metric label="Workers running" value={queues?.is_any_running?'Yes':'No'}/>
          </div>
          <button type="button" className={button} onClick={onOpenQueues}>Open AI queue manager</button>
        </section>}
      </div>
    </section>
  </div>;
}
