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

export default function CatalogWorkflowsPanel({onClose, initialTab='products',onOpenSources,onOpenReviews,onOpenIngredients,onOpenQueues}) {
  const [tab,setTab]=useState(initialTab);
  const [sources,setSources]=useState([]);
  const [sourceId,setSourceId]=useState('sourcebeauty');
  const [multiSource,setMultiSource]=useState(true);
  const [selectedSources,setSelectedSources]=useState([]);
  const [discoveryJob,setDiscoveryJob]=useState(null);
  const [jobStatus,setJobStatus]=useState(null);
  const [method,setMethod]=useState('shopify');
  const [startPage,setStartPage]=useState(1);
  const [pages,setPages]=useState(1);
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
  const [scope,setScope]=useState('unmatched_only');
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
          source_ids:selectedSources,start_page:Number(startPage),pages:Number(pages),
          dry_run:preview,urls_by_source:urlsBySource
        }));
        setJobStatus(null);setDiscoveryJob(response.task_id);return;
      } else if(tab==='products' && method==='shopify') {
        response=await call('/admin/workflows/products/discover',json({
          source_id:sourceId,start_page:Number(startPage),pages:Number(pages),dry_run:preview
        }));
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
              <label className="text-xs font-semibold">Starting page<input className={input} type="number" min="1" max="5000" value={startPage} onChange={e=>setStartPage(e.target.value)}/></label>
              <label className="text-xs font-semibold">Pages per Shopify source (1–3)<input className={input} type="number" min="1" max="3" value={pages} onChange={e=>setPages(e.target.value)}/></label>
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
            <h4 className="font-bold">Multi-source discovery: {jobStatus.status}</h4>
            <p className="text-xs text-gray-600">{jobStatus.processed_items||0} / {jobStatus.total_items||selectedSources.length} sources finished</p>
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
          {tab==='products'&&<section className="rounded-xl border bg-white p-5 space-y-3">
            <h3 className="font-bold text-gray-900">Check existing products automatically</h3>
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
          <button type="button" className={button} onClick={onOpenIngredients}>Open ingredient lookup</button>
        </section>}
        {tab==='llm'&&<section className="max-w-3xl mx-auto rounded-xl border bg-white p-5 space-y-4">
          <h3 className="text-lg font-bold">AI review queues</h3>
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
