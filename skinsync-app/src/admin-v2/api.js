import { apiFetch } from '../apiFetch';

export const API = 'http://127.0.0.1:8000';

export async function request(path, options = {}) {
  const response = await apiFetch(API + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || response.statusText || 'Request failed');
  }
  return data;
}

export const post = (path, payload = {}) => request(path, {
  method: 'POST', body: JSON.stringify(payload),
});
export const put = (path, payload = {}) => request(path, {
  method: 'PUT', body: JSON.stringify(payload),
});

export function formatNumber(value) {
  return typeof value === 'number' ? value.toLocaleString() : (value ?? '—');
}

export function useInterval(callback, delay) {
  // Stable effect subscriptions are managed in the component by the caller.
  void callback; void delay;
}
