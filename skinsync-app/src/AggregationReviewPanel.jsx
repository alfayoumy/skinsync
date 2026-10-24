import { apiFetch } from './apiFetch';
import { useCallback, useEffect, useRef, useState } from 'react';
import { TestResult } from './ConnectionTests';
import useConnectionTests from './useConnectionTests';
const API = 'http://127.0.0.1:8000/admin/aggregation';
const categories = ['Cleanser', 'Moisturizer', 'Serum', 'Sunscreen', 'Toner', 'Exfoliator', 'Eye Skincare', 'Lip Care', 'Mask', 'Treatment', 'Body Care', 'Tanning', 'Set', 'Makeup', 'Hair Care', 'Fragrance'];
const field = 'w-full border border-gray-300 rounded-lg p-2 bg-white text-sm';
async function api(path, options = {}) {
  const response = await apiFetch(`${API}${path}`, { ...options, headers: { 'Content-Type': 'application/json' } });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed.');
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
  const [canonicalId, setCanonicalId] = useState(''), [category, setCategory] = useState('Moisturizer');
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const refresh = useCallback(async () => {
    const [registry, queue] = await Promise.all([api('/sources'), api(`/reviews?status=pending&offset=${offset}&limit=50${kind ? `&kind=${kind}` : ''}`)]);
    setSources(registry.sources); setReviews(queue.reviews); setTotal(queue.total);
  }, [offset, kind]);
  useEffect(() => { dialog.current.showModal(); }, []);
  useEffect(() => { refresh().catch(e => setError(e.message)); }, [refresh]);
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
      {error && <p role="alert" className="bg-red-50 text-red-800 p-3 rounded-lg">{error}</p>}{message && <p role="status" className="bg-green-50 text-green-800 p-3 rounded-lg">{message}</p>}
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
      {mode === 'reviews' && <section className="grid md:grid-cols-2 gap-4"><div className="bg-white border rounded-xl p-4"><div className="flex justify-between mb-3"><h3 className="font-semibold">Pending reviews ({total})</h3><select aria-label="Review type" value={kind} onChange={e => { setKind(e.target.value); setOffset(0); setSelected(null); }} className="border rounded p-1"><option value="">All types</option>{['identity', 'duplicate', 'category', 'formulation'].map(k => <option key={k}>{k}</option>)}</select></div>
        <ul className="space-y-2 max-h-96 overflow-auto">{reviews.map(r => <li key={r.id}><button className={`w-full text-left border rounded-lg p-3 ${selected?.id === r.id ? 'bg-purple-50 border-purple-500' : ''}`} onClick={() => { setSelected(r); setAction('request_evidence'); setCanonicalId(r.canonical_product_id || ''); }}><span className="block text-xs text-purple-700 uppercase">{r.kind} · {r.source_id || 'Canonical evidence'}</span><span className="block text-sm font-medium">{r.details.title || r.details.original_title || r.details.details?.titles?.join(' / ') || `Canonical ${r.canonical_product_id || 'unresolved'}`}</span></button></li>)}</ul>
        {!reviews.length && <p className="py-8 text-sm text-gray-500">No pending reviews in this view.</p>}<div className="flex justify-between text-sm mt-3"><button disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button><span>{Math.min(offset + reviews.length, total)} of {total}</span><button disabled={offset + 50 >= total || busy} onClick={() => setOffset(offset + 50)}>Next</button></div>
      </div><div className="bg-white border rounded-xl p-4">{selected ? <form className="space-y-3" onSubmit={e => { e.preventDefault(); perform(async () => { await api(`/reviews/${selected.id}`, { method: 'POST', body: JSON.stringify({ action, reviewer, reason, canonical_id: canonicalId ? Number(canonicalId) : null, category }) }); setMessage('Review saved. Re-ingest the observation to apply an accepted mapping.'); setSelected(null); setReason(''); }); }}>
        <h3 className="font-semibold">Review #{selected.id}</h3>{selected.details.source_url && <a className="text-purple-700 underline text-sm" target="_blank" rel="noreferrer" href={selected.details.source_url}>Open source declaration</a>}
        <details open><summary className="text-sm cursor-pointer">Identity and ingredient evidence</summary><pre className="text-xs whitespace-pre-wrap bg-gray-50 rounded p-3 overflow-auto max-h-64">{JSON.stringify(selected.details, null, 2)}</pre></details>
        <label className="block text-sm">Decision<select value={action} onChange={e => setAction(e.target.value)} className={field}><option value="request_evidence">Request further evidence</option><option value="dismiss">Dismiss finding</option><option value="keep_separate">Keep products separate</option>{canLink && <><option value="link">Accept canonical mapping</option><option value="cannot_link">Reject canonical mapping</option></>}<option value="category">Approve category</option></select></label>
        {['link', 'cannot_link'].includes(action) && <label className="block text-sm">Canonical ID<input required min="1" type="number" className={field} value={canonicalId} onChange={e => setCanonicalId(e.target.value)} /></label>}
        {action === 'category' && <label className="block text-sm">Category<select className={field} value={category} onChange={e => setCategory(e.target.value)}>{categories.map(c => <option key={c}>{c}</option>)}</select></label>}
        <label className="block text-sm">Reviewer<input required className={field} value={reviewer} onChange={e => setReviewer(e.target.value)} /></label><label className="block text-sm">Evidence and reason<textarea required rows="3" className={field} value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={busy} className="rounded-lg bg-purple-700 text-white px-4 py-2 disabled:opacity-50">Save review decision</button>
      </form> : <p className="text-gray-500 text-sm py-10">Select a finding to inspect source evidence and proposed actions.</p>}</div></section>}
    </main>
  </dialog>;
}
