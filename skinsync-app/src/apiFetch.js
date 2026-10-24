let adminToken = '';
export function setAdminToken(value) { adminToken = value; }
export async function apiFetch(input, options = {}) {
  const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
  const headers = new Headers(options.headers);
  if (adminToken && ['http://127.0.0.1:8000', 'http://localhost:8000'].includes(url.origin)) {
    headers.set('Authorization', `Bearer ${adminToken}`);
  }
  const response = await fetch(input, { ...options, headers });
  if (response.status === 401 && url.pathname.startsWith('/admin')) {
    window.dispatchEvent(new Event('skinsync-admin-unauthorized'));
  }
  return response;
}
