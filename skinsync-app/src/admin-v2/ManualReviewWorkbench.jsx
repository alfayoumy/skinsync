import {useEffect,useState} from 'react';
import {Check, X, SkipForward, Flame, Trophy, ExternalLink, ShieldAlert,
  Layers3, FlaskConical, ImageOff, RefreshCw, CircleHelp, ArrowLeftRight} from 'lucide-react';
import {request,post} from './api';

const cards='min-w-0 rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-sm';
const pretty=(key)=>key.replace(/_/g,' ').replace(/\b\w/g,s=>s.toUpperCase());
const display=(v)=>v===null||v===undefined||v===''?'Not supplied':typeof v==='object'?JSON.stringify(v):String(v);
const pct=(v)=>typeof v==='number'?(v*100).toFixed(0)+'%':'Not available';

function EvidencePhoto({url,title}) {
  const [broken,setBroken]=useState(false);
  return <div className="w-full h-48 sm:h-60 bg-slate-100 flex items-center justify-center overflow-hidden">
    {url&&!broken?<img src={url} alt={title||'Product'} loading="lazy"
      className="h-full max-w-full object-contain" onError={()=>setBroken(true)}/>:
      <div className="text-slate-400 flex flex-col items-center gap-2 text-xs"><ImageOff size={34}/>No verified product image</div>}
  </div>;
}
function Fact({name,value}) {
  if(value===undefined||value===null||value==='')return null;
  return <div className="flex justify-between gap-4 py-2 border-b border-slate-100 text-sm">
    <dt className="text-slate-500 shrink-0">{name}</dt>
    <dd className="font-semibold text-right break-words min-w-0">{display(value)}</dd></div>;
}
function AttributeList({values,label}) {
  const entries=Object.entries(values||{}).filter(([,v])=>v!==null&&v!==''&&v!==undefined).slice(0,18);
  return <section className="mt-4"><h4 className="font-semibold text-xs uppercase tracking-wide text-slate-500">{label}</h4>
    {entries.length?<dl className="mt-2 divide-y">{entries.map(([k,v])=>
      <Fact key={k} name={pretty(k)} value={v}/>)}</dl>:
      <p className="text-sm text-slate-500 mt-2">No structured variant identifiers recorded.</p>}</section>;
}
function ProductEvidence({item,kind,side}) {
  const left=side==='left';
  return <article className={cards}>
    <div className="flex justify-between gap-3 bg-slate-50 p-4 items-center">
      <span className="text-xs font-extrabold tracking-wide uppercase text-slate-600">{item.role}</span>
      {item.url?<a href={item.url} target="_blank" rel="noreferrer noopener"
        className="inline-flex gap-1.5 text-violet-700 text-xs font-semibold underline underline-offset-4">
        Open original <ExternalLink size={14}/></a>:<span className="text-xs text-amber-700">No verified source link</span>}
    </div>
    <EvidencePhoto key={item.image||"missing"} url={item.image} title={item.title}/>
    <div className="px-5 pb-5 pt-4">
      <p className="text-xs uppercase tracking-wide text-slate-500 font-bold">{item.brand||'Brand unverified'}</p>
      <h3 className="text-lg font-bold leading-snug mt-1 min-h-12">{item.title||'Missing product title'}</h3>
      <dl className="mt-4">
        <Fact name="Source" value={item.retailer}/><Fact name="Market" value={item.market}/>
        <Fact name="Category" value={item.category}/>
        <Fact name="Identity status" value={item.identity_status}/>
        {kind==='identity'&&<><Fact name="GTIN / barcode" value={item.gtin}/><Fact name="SKU" value={item.sku}/>
          <Fact name="Canonical ID" value={item.canonical_id}/>
          <Fact name="Listed price" value={item.price!=null?`${item.price} ${item.currency||''}`:null}/></>}
        {kind==='inci'&&!left&&<><Fact name="INCI completeness" value={item.completeness}/>
          <Fact name="Combined confidence" value={pct(item.confidence)}/>
          <Fact name="Identity confidence" value={pct(item.identity_confidence)}/>
          <Fact name="Source confidence" value={pct(item.source_confidence)}/>
          <Fact name="Extraction model" value={item.model_name}/></>}
      </dl>
      <AttributeList values={item.attributes} label={kind==='identity'?'Variant / identity attributes':'Formulation and source metadata'}/>
      {(kind==='inci'||item.ingredients)&&<section className="rounded-xl bg-slate-50 border border-slate-200 p-4 mt-4">
        <h4 className="font-bold text-sm">{kind==='inci'?(left?'Currently published INCI':'Proposed complete INCI'):'Ingredient evidence (not identity proof)'}</h4>
        <p className="text-sm whitespace-pre-wrap break-words leading-relaxed mt-2">
          {item.ingredients||'No existing full INCI published for this product.'}</p>
        {kind==='inci'&&!left&&item.ingredient_tokens?.length>0&&
          <p className="text-xs text-slate-600 mt-2">{item.ingredient_tokens.length} extracted ingredient tokens · order retained</p>}
      </section>}
      {kind==='inci'&&!left&&item.evidence_excerpt&&<section className="mt-4 rounded-xl p-3 border border-slate-200 text-xs">
        <h4 className="font-bold mb-2">Source-page evidence excerpt</h4>
        <p className="whitespace-pre-wrap break-words">{item.evidence_excerpt}</p>
      </section>}
    </div>
  </article>;
}
const sessionDefaults={match:0,no_match:0,skip:0};
export default function ManualReviewWorkbench({actor}) {
  const [kind,setKind]=useState('identity');
  const [view,setView]=useState('ready');
  const [items,setItems]=useState([]);
  const [counts,setCounts]=useState(null);
  const [index,setIndex]=useState(0);
  const [notes,setNotes]=useState('');
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  const [session,setSession]=useState(sessionDefaults);
  const [streak,setStreak]=useState(0);
  const [reviewer,setReviewer]=useState(actor?.name||'');
  const [fetching,setFetching]=useState(true);
  async function load(queue=kind) {
    setFetching(true);
    try {
      const data=await request('/admin/v2/manual-reviews?kind='+encodeURIComponent(queue)+'&view='+view+'&limit=30');
      setItems(data.items||[]);setCounts(data.counts);setReviewer(data.reviewer);
      setIndex(0);setNotes('');setError('');
    }catch(err){setError(err.message);}finally{setFetching(false);}
  }
  useEffect(()=>{
    let enabled=true;
    async function fetchQueue() {
      setFetching(true);
      try{
        const data=await request('/admin/v2/manual-reviews?kind='+encodeURIComponent(kind)+'&view='+view+'&limit=30');
        if(enabled){setItems(data.items||[]);setCounts(data.counts);setReviewer(data.reviewer);
          setIndex(0);setNotes('');setError('');}
      }catch(e){if(enabled)setError(e.message);}
      finally{if(enabled)setFetching(false);}
    }
    fetchQueue();
    return ()=>{enabled=false};
  },[kind,view]);
  const current=items[index];
  async function choose(action) {
    if(!current||busy)return;
    setBusy(true);setError('');setMessage('');
    try{
      const result=await post('/admin/v2/manual-reviews/'+kind+'/'+current.id,{
        action,fingerprint:current.fingerprint,comment:notes.trim()
      });
      setSession(s=>({...s,[action]:s[action]+1}));
      setStreak(x=>action==='skip'?0:x+1);
      setMessage(action==='skip'?'Skipped for your account; unresolved for other reviewers.':
        action==='match'?'Match recorded with existing domain safety gates.':'No Match recorded.');
      setNotes('');
      // A fresh server read ensures an answered case cannot be voted on twice
      // and skips remain scoped to this signed-in reviewer.
      const data=await request('/admin/v2/manual-reviews?kind='+encodeURIComponent(kind)+'&limit=30');
      setItems(data.items||[]);setCounts(data.counts);setIndex(0);
      setReviewer(data.reviewer||result.reviewer);
    }catch(e){setError(e.message);}
    finally{setBusy(false);}
  }
  const totalSession=session.match+session.no_match+session.skip;
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="uppercase tracking-[.16em] text-xs font-bold text-violet-600">SkinSync / Human review</p>
        <h2 className="text-2xl font-extrabold mt-1">Match Arena</h2>
        <p className="text-slate-500 text-sm mt-1">One evidence-backed pair at a time. Reviewer: <strong className="text-slate-800">{reviewer||'Signed-in administrator'}</strong></p></div>
      <div className="flex gap-2 flex-wrap">
        <div className="rounded-xl bg-white border p-3 min-w-24">
          <div className="flex items-center gap-2 text-amber-600"><Flame size={17}/><strong>{streak}</strong></div>
          <div className="text-xs text-slate-500">Current streak</div></div>
        <div className="rounded-xl bg-white border p-3 min-w-24">
          <div className="flex items-center gap-2 text-violet-700"><Trophy size={17}/><strong>{(session.match+session.no_match)*10}</strong></div>
          <div className="text-xs text-slate-500">Completed decision points</div></div>
      </div>
    </header>
    <nav aria-label="Manual review queue" className="flex gap-2 flex-wrap">
      <button onClick={()=>setKind('identity')} aria-pressed={kind==='identity'}
        className={`rounded-xl px-4 py-3 text-sm font-bold inline-flex gap-2 items-center ${kind==='identity'?'bg-violet-700 text-white':'bg-white border border-slate-200'}`}>
        <Layers3 size={17}/>Product identity</button>
      <button onClick={()=>setKind('inci')} aria-pressed={kind==='inci'}
        className={`rounded-xl px-4 py-3 text-sm font-bold inline-flex gap-2 items-center ${kind==='inci'?'bg-violet-700 text-white':'bg-white border border-slate-200'}`}>
        <FlaskConical size={17}/>INCI formulation</button>
    </nav>
    <label className="flex items-center gap-3 text-sm font-semibold">Review population
      <select className="rounded-xl bg-white border border-slate-300 px-3 py-2 text-sm" value={view}
        onChange={e=>setView(e.target.value)}>
        <option value="ready">Ready for a decision</option>
        <option value="skipped">Skipped by me (revisit)</option>
      </select>
    </label>
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
      {[
        ['Ready to compare',counts?.ready],['Needs advanced review',counts?.advanced],
        ['Skipped by you',counts?.skipped_by_you],['Your session decisions',totalSession]
      ].map(([label,value])=><div key={label} className="rounded-xl border bg-white p-3">
        <div className="text-2xl font-black">{value??'—'}</div><div className="text-xs text-slate-500">{label}</div></div>)}
    </div>
    {counts?.partial_scan&&<p className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-amber-900 text-xs">
      Queue counts reflect the first 500 pending cases only. Full backlog and advanced exceptions remain in specialized review tools.</p>}
    {error&&<p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-red-800 text-sm">{error}</p>}
    {message&&<p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-emerald-900 text-sm">{message}</p>}
    {fetching?<div className="border rounded-2xl bg-white p-10 text-center text-slate-500">Loading saved queue evidence…</div>:
      !current?<section className="rounded-2xl bg-white border text-center py-16 px-6">
        <Trophy className="mx-auto text-amber-500" size={40}/>
        <h3 className="font-bold text-xl mt-4">No more pairs in this view</h3>
        <p className="text-sm text-slate-500 mt-2">Complex variants, out-of-market formulations and skipped items may still require attention. They have not been auto-approved.</p>
        <button onClick={()=>load()} className="mt-5 text-violet-700 font-semibold text-sm"><RefreshCw className="inline mr-2" size={16}/>Refresh queue</button>
      </section>:
      <div className="space-y-4">
        <div className="rounded-xl border border-violet-100 bg-violet-50 p-4 flex items-center justify-between gap-3">
          <div><p className="font-bold text-violet-900">Comparison {index+1} · Case #{current.id}</p>
            <p className="text-xs text-violet-800 mt-1">{kind==='identity'?'Are these the same exact product and variant?':'Does this source show the exact complete formulation for this product and market?'}</p></div>
          <ArrowLeftRight className="text-violet-600 shrink-0" size={24}/>
        </div>
        {kind==='identity'&&current.match_blockers?.length>0&&<p className="rounded-xl bg-amber-50 border border-amber-200 p-4 text-sm text-amber-900 flex gap-2">
          <ShieldAlert className="shrink-0" size={18}/>Match blocked: {current.match_blockers.join(' · ')}. No Match and Skip remain available.</p>}
        {kind==='inci'&&current.requires_market_confirmation&&<p className="rounded-xl bg-amber-50 border border-amber-200 p-4 text-sm text-amber-900 flex gap-2">
          <ShieldAlert className="shrink-0" size={18}/>Market compatibility is not established. A quick Match is blocked; specialist review must explicitly validate regional equivalence.</p>}
        <div className="grid md:grid-cols-2 gap-4">
          <ProductEvidence item={current.left} kind={kind} side="left"/>
          <ProductEvidence item={current.right} kind={kind} side="right"/>
        </div>
        {current.model?.verdict&&<div className="rounded-xl p-3 border bg-slate-50 text-sm">
          <CircleHelp size={16} className="inline mr-2"/><b>Prior model suggestion:</b> {current.model.verdict} · {pct(current.model.confidence)}
          <p className="text-xs text-slate-600 mt-2">{display(current.model.explanation)}</p>
          <p className="text-xs text-slate-500 mt-2">Model suggestions are evidence, not authority.</p>
        </div>}
        {current.notes&&<p className="text-xs text-slate-600">Review context: {current.notes}</p>}
        <div className="sticky bottom-3 z-10 rounded-2xl border border-slate-200 shadow-xl bg-white p-4 sm:p-5 space-y-3">
          <label className="block text-sm font-semibold">Optional reviewer comment
            <textarea value={notes} onChange={e=>setNotes(e.target.value)} rows={2} maxLength={1500}
              className="block mt-2 w-full resize-y rounded-xl border border-slate-300 p-3 text-sm"
              placeholder="Reason, observed differences, or evidence gaps (optional)"/>
          </label>
          <div className="grid grid-cols-3 gap-2 sm:gap-3">
            <button type="button" disabled={busy||(current.requires_market_confirmation&&kind==='inci')||(kind==='identity'&&current.match_blockers?.length>0)}
              onClick={()=>choose('match')} className="rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white py-4 font-extrabold text-sm sm:text-base disabled:opacity-40 flex items-center justify-center gap-2">
              <Check size={20}/>Match</button>
            <button type="button" disabled={busy} onClick={()=>choose('no_match')}
              className="rounded-xl bg-rose-600 hover:bg-rose-700 text-white py-4 font-extrabold text-sm sm:text-base disabled:opacity-40 flex items-center justify-center gap-2">
              <X size={20}/>No Match</button>
            <button type="button" disabled={busy} onClick={()=>choose('skip')}
              className="rounded-xl bg-slate-200 hover:bg-slate-300 text-slate-900 py-4 font-extrabold text-sm sm:text-base disabled:opacity-40 flex items-center justify-center gap-2">
              <SkipForward size={18}/>Skip</button>
          </div>
          <p className="text-xs text-slate-500 text-center">
            {kind==='identity'?'Match records an identity link; No Match records an explicit rejected pairing. Neither automatically merges canonicals or publishes INCI.':
              'Match attempts guarded full-formulation approval/publication. No Match rejects only this research candidate. Skip keeps the case open.'}
          </p>
        </div>
      </div>}
  </div>;
}
