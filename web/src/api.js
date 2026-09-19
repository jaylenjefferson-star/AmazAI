import { idToken } from './auth';

const BASE = import.meta.env.VITE_API_URL?.replace(/\/$/, '') ?? '';

async function call(method, path, body) {
  const token = await idToken();
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { authorization: token, 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    throw new Error(data?.detail || data?.error || `${res.status} ${res.statusText}`);
  }
  return data;
}

export const api = {
  agents: () => call('GET', '/agents'),
  agent: (id) => call('GET', `/agents/${id}`),
  updateAgent: (id, changes) => call('PATCH', `/agents/${id}`, changes),
  addMemory: (id, entry) => call('POST', `/agents/${id}/memory`, entry),
  deleteMemory: (id, memId) => call('DELETE', `/agents/${id}/memory/${memId}`),

  threads: () => call('GET', '/threads'),
  thread: (id) => call('GET', `/threads/${id}`),
  createThread: (t) => call('POST', '/threads', t),
  send: (id, text) => call('POST', `/threads/${id}/messages`, { text }),
  exec: (id, command) => call('POST', `/threads/${id}/exec`, { command }),

  run: (id) => call('GET', `/runs/${id}`),
  cancel: (id) => call('POST', `/runs/${id}/cancel`, {}),
  decide: (runId, apvId, approve, note) =>
    call('POST', `/approvals/${runId}/${apvId}`, { approve, note }),

  usage: (agentId, month) =>
    call('GET', `/usage?agentId=${encodeURIComponent(agentId)}${month ? `&month=${month}` : ''}`),
};
