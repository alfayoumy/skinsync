import { useEffect, useState } from 'react';
const endpoint = 'http://127.0.0.1:8000/admin/aggregation/connection-tests';
export default function useConnectionTests() {
  const [state, setState] = useState({ results: {}, running: [] });
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const response = await fetch(endpoint);
        if (!response.ok) throw new Error('Could not load test results.');
        const data = await response.json();
        if (active) setState(data);
      } catch (e) { if (active) setError(e.message); }
    }
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  async function test(kind, ids = [], sample_url = null) {
    setError('');
    try {
      const response = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, ids, sample_url }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not start test.');
      setState(data);
    } catch (e) { setError(e.message); }
  }
  return { ...state, test, error };
}
