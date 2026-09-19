import { accessToken } from './auth0';
import { DEMO, demoApi } from './demo';

const BASE = import.meta.env.VITE_API_URL?.replace(/\/$/, '') ?? '';

async function call(method, path, body, extraHeaders) {
  const token = await accessToken();
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`, 'content-type': 'application/json', ...extraHeaders,
    },
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

const live = {
  agents: () => call('GET', '/agents'),
  agentOptions: () => call('GET', '/agents/options'),
  // The key travels with the request so a retry after a timeout resolves to
  // the agent the first attempt created, rather than a second one.
  createAgent: (agent, idempotencyKey) =>
    call('POST', '/agents', agent, { 'idempotency-key': idempotencyKey }),
  archiveAgent: (id) => call('DELETE', `/agents/${id}`),
  agent: (id) => call('GET', `/agents/${id}`),
  updateAgent: (id, changes) => call('PATCH', `/agents/${id}`, changes),
  addMemory: (id, entry) => call('POST', `/agents/${id}/memory`, entry),
  deleteMemory: (id, memId) => call('DELETE', `/agents/${id}/memory/${memId}`),

  connectorCatalog: () => call('GET', '/connectors/catalog'),
  connectorApps: (q, after) => call('GET', `/connectors/apps?limit=48${q ? `&q=${encodeURIComponent(q)}` : ''}${after ? `&after=${encodeURIComponent(after)}` : ''}`),
  connectors: () => call('GET', '/connectors'),
  connectorAccounts: (app) => call('GET', `/connectors/accounts${app ? `?app=${encodeURIComponent(app)}` : ''}`),
  connectToken: (connectorId) => call('POST', '/connectors/connect-token', { connectorId }),
  installConnector: (connectorId, accountId, allowedTools) =>
    call('POST', `/connectors/${encodeURIComponent(connectorId)}/install`, { accountId, allowedTools }),
  revokeConnector: (connectorId) => call('DELETE', `/connectors/${encodeURIComponent(connectorId)}`),

  threads: () => call('GET', '/threads'),
  thread: (id) => call('GET', `/threads/${id}`),
  createThread: (t) => call('POST', '/threads', t),
  send: (id, text) => call('POST', `/threads/${id}/messages`, { text }),
  exec: (id, command) => call('POST', `/threads/${id}/exec`, { command }),
  // Read-only: agent<->agent handoffs and messages bound to this thread.
  // Never a write path — sender/recipient are the only agents who may
  // address one another here.
  coordination: (id) => call('GET', `/threads/${id}/coordination`),

  run: (id) => call('GET', `/runs/${id}`),
  cancel: (id) => call('POST', `/runs/${id}/cancel`, {}),
  decide: (runId, apvId, approve, note) =>
    call('POST', `/approvals/${runId}/${apvId}`, { approve, note }),
  // Every pending approval, across every run — what the Home inbox and
  // Rooms need without polling each run individually.
  approvals: (status = 'pending') =>
    call('GET', `/approvals${status ? `?status=${encodeURIComponent(status)}` : ''}`),

  skills: () => call('GET', '/skills'),
  skillVersions: (id) => call('GET', `/skills/${id}/versions`),
  assignSkill: (skillId, agentId, version) =>
    call('POST', `/skills/${skillId}/assignments`, { agentId, version }),
  unassignSkill: (skillId, agentId) =>
    call('DELETE', `/skills/${skillId}/assignments/${agentId}`),

  usage: (agentId, month) =>
    call('GET', `/usage?agentId=${encodeURIComponent(agentId)}${month ? `&month=${month}` : ''}`),
};

// In demo mode the console runs against fixtures instead of the control
// plane. `import.meta.env.DEV` inside demo.js keeps this out of a prod build.
export const api = DEMO ? demoApi : live;
