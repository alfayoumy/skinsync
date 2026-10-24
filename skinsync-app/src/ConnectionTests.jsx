import useConnectionTests from './useConnectionTests';
export function TestResult({ result, running }) {
  if (!result && !running) return <p className="text-xs text-gray-500">Not tested</p>;
  return <div className="text-xs space-y-1 mt-2" aria-live="polite">
    {running && <p className="text-purple-700">Queued / testing…</p>}
    {result && <><p>Online: <strong>{result.online.replaceAll('_', ' ')}</strong></p><p>Function: <strong>{result.functional.replaceAll('_', ' ')}</strong></p>
      <p className="text-gray-600">{result.detail}</p>
      {result.products !== undefined && <p>{result.products} products · {result.priced_products} priced · {result.ordered_inci_products} ordered INCI</p>}
      <p className="text-gray-500">{new Date(result.checked_at).toLocaleString()} · {result.duration_seconds}s</p>
      {result.url && <a className="underline" href={result.url} target="_blank" rel="noreferrer">Tested sample</a>}</>}
  </div>;
}
export default function ApiConnectionTests() {
  const diagnostics = useConnectionTests();
  return <section className="p-4 border rounded-xl bg-white space-y-3">
    <div className="flex justify-between"><h3 className="font-semibold">Live API tests</h3><button disabled={diagnostics.running.some(k => k.startsWith('api:'))} onClick={() => diagnostics.test('api')} className="border rounded px-3 py-1">Test all APIs</button></div>
    <p className="text-xs text-gray-500">Sends one small request per provider and validates its response. Results last until the server restarts.</p>
    {diagnostics.error && <p role="alert">{diagnostics.error}</p>}
    <div className="grid md:grid-cols-3 gap-3">{['gemini', 'jev', 'langsearch'].map(id => <div key={id} className="border rounded-lg p-3"><div className="flex justify-between"><strong className="capitalize">{id}</strong><button disabled={diagnostics.running.includes(`api:${id}`)} onClick={() => diagnostics.test('api', [id])} className="border rounded px-2">Test</button></div><TestResult result={diagnostics.results[`api:${id}`]} running={diagnostics.running.includes(`api:${id}`)} /></div>)}</div>
  </section>;
}
