import { Fragment, useCallback, useEffect, useState } from 'react';
import { apiFetch } from './apiFetch';

const BASE='http://127.0.0.1:8000/admin/aggregation';
const field='w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800';
const button='rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-800 disabled:opacity-50';
const empty={search:'',verdict:'',outcome:'',review_status:'',min:0,max:100,sort_by:'confidence',sort_order:'desc'};
const outcomes={auto_resolved:'Auto-resolved',needs_manual:'Needs review',queued:'Queued',processing:'Processing'};
const verdicts={same_product:'Same product',different_product:'Different products',uncertain:'Uncertain'};
const percentage=v=>v==null?'—':(v*100).toFixed(v===1?0:1)+'%';

export default function GeminiDecisionsTable({refreshKey}) {
  const [draft,setDraft]=useState(empty),[filters,setFilters]=useState(empty);
  const [offset,setOffset]=useState(0),[rows,setRows]=useState([]),[total,setTotal]=useState(0);
  const [loading,setLoading]=useState(true),[error,setError]=useState('');
  const [expanded,setExpanded]=useState(null),[revision,setRevision]=useState(0);
  const pageSize=50;
  const load=useCallback(async()=>{
    setLoading(true);setError('');
    try {
      const params=new URLSearchParams({
        confidence_min:String(Number(filters.min)/100),
        confidence_max:String(Number(filters.max)/100),
        sort_by:filters.sort_by,sort_order:filters.sort_order,
        offset:String(offset),limit:String(pageSize)
      });
      for(const key of ['verdict','outcome','review_status','search']){
        if(filters[key]?.trim())params.set(key,filters[key].trim());
      }
      const response=await apiFetch(BASE+'/reviews/gemini/results?'+params.toString());
      const body=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'HTTP '+response.status);
      setRows(body.results||[]);setTotal(body.total||0);
    }catch(e){setError('Cannot load Gemini decisions: '+e.message);}
    finally{setLoading(false)}
  },[filters,offset,revision,refreshKey]);
  useEffect(()=>{load()},[load]);
  function update(name,value){setDraft(p=>({...p,[name]:value}))}
  function applyFilters(e){
    e.preventDefault();
    if(Number(draft.min)>Number(draft.max)){
      setError('Minimum confidence cannot exceed maximum confidence.');return;
    }
    setExpanded(null);setOffset(0);setFilters({...draft});setRevision(v=>v+1);
  }
  function reset(){setDraft(empty);setFilters(empty);setOffset(0);setExpanded(null);setRevision(v=>v+1)}
  function sort(field){
    const value={...filters,sort_by:field,sort_order:filters.sort_by===field&&filters.sort_order==='desc'?'asc':'desc'};
    setFilters(value);setDraft(value);setOffset(0);setExpanded(null);
  }
  const direction=field=>filters.sort_by===field?(filters.sort_order==='desc'?' ↓':' ↑'):' ↕';
  return <section className="rounded-xl border border-gray-200 bg-white p-4 sm:p-5 space-y-4" aria-label="Gemini decision history">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="text-lg font-bold text-gray-900">Gemini decisions</h3>
        <p className="mt-1 text-sm text-gray-600">Browse every saved verdict, including completed cases. Confidence is the model’s assessment of its verdict, not proof that all listings are identical. Filtering and inspection do not call Gemini.</p></div>
      <button type="button" className={button} onClick={()=>setRevision(v=>v+1)} disabled={loading}>Refresh results</button>
    </div>
    <form className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3" onSubmit={applyFilters}>
      <label className="text-xs font-semibold text-gray-700 col-span-2 md:col-span-2">Product / canonical ID / review ID
        <input type="search" className={field+' mt-1'} value={draft.search}
          onChange={e=>update('search',e.target.value)} placeholder="Avène or 46537"/>
      </label>
      <label className="text-xs font-semibold text-gray-700">Gemini verdict
        <select className={field+' mt-1'} value={draft.verdict} onChange={e=>update('verdict',e.target.value)}>
          <option value="">All</option><option value="same_product">Same product</option>
          <option value="different_product">Different products</option><option value="uncertain">Uncertain</option>
        </select>
      </label>
      <label className="text-xs font-semibold text-gray-700">Application outcome
        <select className={field+' mt-1'} value={draft.outcome} onChange={e=>update('outcome',e.target.value)}>
          <option value="">All</option><option value="auto_resolved">Auto-resolved</option>
          <option value="needs_manual">Needs review</option><option value="queued">Queued</option>
          <option value="processing">Processing</option>
        </select>
      </label>
      <label className="text-xs font-semibold text-gray-700">Review status
        <select className={field+' mt-1'} value={draft.review_status} onChange={e=>update('review_status',e.target.value)}>
          <option value="">All</option><option value="pending">Pending</option><option value="confirmed">Completed</option>
        </select>
      </label>
      <label className="text-xs font-semibold text-gray-700">Sort by
        <select className={field+' mt-1'} value={draft.sort_by} onChange={e=>update('sort_by',e.target.value)}>
          <option value="confidence">Confidence</option><option value="updated_at">Updated</option>
          <option value="review_id">Review ID</option><option value="verdict">Verdict</option>
          <option value="outcome">Outcome</option>
        </select>
      </label>
      <label className="text-xs font-semibold text-gray-700">Min confidence %
        <input className={field+' mt-1'} type="number" min="0" max="100" step="1" value={draft.min}
          onChange={e=>update('min',e.target.value)}/>
      </label>
      <label className="text-xs font-semibold text-gray-700">Max confidence %
        <input className={field+' mt-1'} type="number" min="0" max="100" step="1" value={draft.max}
          onChange={e=>update('max',e.target.value)}/>
      </label>
      <label className="text-xs font-semibold text-gray-700">Sort direction
        <select className={field+' mt-1'} value={draft.sort_order} onChange={e=>update('sort_order',e.target.value)}>
          <option value="desc">Highest / newest first</option><option value="asc">Lowest / oldest first</option>
        </select>
      </label>
      <div className="col-span-2 md:col-span-3 flex items-end gap-2">
        <button type="submit" className="rounded-lg bg-purple-700 px-4 py-2 text-sm font-semibold text-white">Apply filters</button>
        <button type="button" className={button} onClick={reset}>Clear</button>
      </div>
    </form>
    <div className="flex items-center justify-between gap-2 text-xs text-gray-600">
      <span>{loading?'Loading...':total.toLocaleString()+' saved decisions match'}</span>
      <span>Gemini confidence is model-reported, not independently calibrated.</span>
    </div>
    {error&&<div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
      {error} <button type="button" onClick={()=>setRevision(v=>v+1)} className="font-semibold underline">Retry</button>
    </div>}
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-[850px] w-full text-sm text-left">
        <thead className="bg-gray-50 text-gray-700"><tr>
          <th className="p-3"><button type="button" onClick={()=>sort('review_id')}>Review{direction('review_id')}</button></th>
          <th className="p-3">Product / brand</th>
          <th className="p-3"><button type="button" onClick={()=>sort('verdict')}>Verdict{direction('verdict')}</button></th>
          <th className="p-3"><button type="button" onClick={()=>sort('confidence')}>Confidence{direction('confidence')}</button></th>
          <th className="p-3"><button type="button" onClick={()=>sort('outcome')}>Outcome{direction('outcome')}</button></th>
          <th className="p-3">Applied action</th><th className="p-3">Details</th>
        </tr></thead>
        <tbody className="divide-y divide-gray-200">
          {rows.map(row=><Fragment key={row.review_id}>
            <tr className="hover:bg-gray-50 align-top">
              <td className="p-3 font-semibold">#{row.review_id}</td>
              <td className="p-3 max-w-64"><div className="font-medium text-gray-900">{row.title}</div>
                <div className="text-xs text-gray-500">{row.brand||'Unverified brand'} · Canonical #{row.canonical_product_id??'—'}</div></td>
              <td className="p-3">{verdicts[row.verdict]||row.verdict}</td>
              <td className="p-3 font-bold tabular-nums">{percentage(row.confidence)}</td>
              <td className="p-3">{outcomes[row.outcome]||row.outcome}
                <span className="block text-xs text-gray-500">{row.review_status}</span></td>
              <td className="p-3 text-xs">{row.decision?.action?.replaceAll('_',' ')||'Not applied'}</td>
              <td className="p-3"><button type="button" className="font-semibold text-purple-700 underline"
                aria-expanded={expanded===row.review_id} onClick={()=>setExpanded(p=>p===row.review_id?null:row.review_id)}>
                {expanded===row.review_id?'Hide':'Inspect'}</button></td>
            </tr>
            {expanded===row.review_id&&<tr className="bg-purple-50/40"><td colSpan={7} className="p-4">
              <div className="grid md:grid-cols-2 gap-4 text-xs">
                <div className="space-y-2">
                  <h4 className="font-bold text-gray-900">Gemini explanation</h4>
                  <p className="text-gray-800 whitespace-pre-wrap">{row.model_reason||'No explanation saved.'}</p>
                  {row.application_reason&&<div className="rounded-lg bg-white border p-2 text-gray-700">
                    <strong>SkinSync application check:</strong> {row.application_reason}
                  </div>}
                  <p className="text-gray-600">Model: {row.model_name||'Not recorded'} · Updated: {row.updated_at||'—'} · Attempts: {row.attempts||0}</p>
                  <p className="text-gray-600">Original issue: {row.issue?.replaceAll('_',' ')||'Not recorded'}</p>
                  {!row.evidence_current&&<p className="text-amber-800 font-semibold">Evidence changed after Gemini evaluated it. Automatic application is blocked.</p>}
                  {row.review_notes&&<p className="text-gray-700"><strong>Application notes:</strong> {row.review_notes}</p>}
                </div>
                <div className="space-y-2"><h4 className="font-bold text-gray-900">Store listings ({row.offers?.length||0})</h4>
                  <ul className="space-y-1 max-h-52 overflow-auto">{(row.offers||[]).map((offer,i)=>
                    <li key={offer.product_id??i} className="bg-white border rounded-lg p-2">
                      <strong>#{offer.product_id} · {offer.store_id||'Unknown store'}</strong>
                      <span className="block">{offer.title}</span>
                    </li>)}</ul>
                  {row.decision&&<div className="bg-white border rounded-lg p-2">
                    <strong>Applied action:</strong> {row.decision.action?.replaceAll('_',' ')||'—'}
                    {row.decision.new_canonical_ids&&<span className="block">New canonical IDs: {row.decision.new_canonical_ids.join(', ')}</span>}
                  </div>}
                </div>
              </div>
            </td></tr>}
          </Fragment>)}
          {!loading&&!rows.length&&<tr><td colSpan={7} className="p-8 text-center text-gray-500">No saved Gemini verdicts match the filters.</td></tr>}
        </tbody>
      </table>
    </div>
    <div className="flex items-center justify-between gap-2 text-sm text-gray-700">
      <button type="button" className={button} disabled={loading||offset===0}
        onClick={()=>{setOffset(Math.max(0,offset-pageSize));setExpanded(null)}}>Previous</button>
      <span>{total?String(offset+1)+'–'+String(Math.min(offset+pageSize,total))+' of '+total:'0 results'}</span>
      <button type="button" className={button} disabled={loading||offset+pageSize>=total}
        onClick={()=>{setOffset(offset+pageSize);setExpanded(null)}}>Next</button>
    </div>
  </section>;
}
