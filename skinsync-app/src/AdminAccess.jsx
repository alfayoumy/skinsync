import { useEffect, useState } from 'react';
import { apiFetch, setAdminToken } from './apiFetch';
import AdminV2 from './admin-v2/AdminV2';
export default function AdminAccess({ onBackToApp }) {
  const [token, setToken] = useState('');
  const [actor, setActor] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const expire = () => { setActor(null); setAdminToken(''); };
    window.addEventListener('skinsync-admin-unauthorized', expire);
    return () => { setAdminToken(''); window.removeEventListener('skinsync-admin-unauthorized', expire); };
  }, []);
  if (actor) return <><div className="p-2 flex justify-end gap-3 text-sm bg-purple-50"><span>Administrator: {actor.name}</span><button onClick={() => { setActor(null); setAdminToken(''); }}>Sign out</button></div><AdminV2 onBackToApp={onBackToApp} /></>;
  return <main className="max-w-md mx-auto my-16 p-6 bg-white rounded-xl border space-y-4"><h1 className="text-xl font-bold">Administrator access</h1><form className="space-y-4" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError(''); setAdminToken(token);
    try {
      const response = await apiFetch('http://127.0.0.1:8000/admin/access');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Access denied.');
      setActor(data); setToken('');
    } catch (e) { setAdminToken(''); setError(e.message); } finally { setBusy(false); }
  }}><label className="block">Access token<input autoComplete="off" required type="password" className="block border rounded p-2 w-full" value={token} onChange={e => setToken(e.target.value)} /></label>{error && <p role="alert">{error}</p>}<button disabled={busy} className="border rounded px-3 py-2">{busy ? 'Signing in…' : 'Sign in'}</button></form><button onClick={onBackToApp}>Back to catalog</button></main>;
}
