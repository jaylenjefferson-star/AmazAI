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
  // A Bot's access to one app: full (default) or read-only. Removing takes it away.
  setGrant: (agentId, connectorId, body) =>
    call('PUT', `/agents/${agentId}/grants/${encodeURIComponent(connectorId)}`, body),
  removeGrant: (agentId, connectorId) =>
    call('DELETE', `/agents/${agentId}/grants/${encodeURIComponent(connectorId)}`),
  addMemory: (id, entry) => call('POST', `/agents/${id}/memory`, entry),
  deleteMemory: (id, memId) => call('DELETE', `/agents/${id}/memory/${memId}`),
  updateMemory: (id, memId, changes) => call('PATCH', `/agents/${id}/memory/${memId}`, changes),
  sharedMemory: () => call('GET', '/memory'),
  addSharedMemory: (entry) => call('POST', '/memory', entry),
  deleteSharedMemory: (memId) => call('DELETE', `/memory/${memId}`),
  updateSharedMemory: (memId, changes) => call('PATCH', `/memory/${memId}`, changes),

  connectorApps: (q, after) => call('GET', `/connectors/apps?limit=48${q ? `&q=${encodeURIComponent(q)}` : ''}${after ? `&after=${encodeURIComponent(after)}` : ''}`),
  connectors: () => call('GET', '/connectors'),
  connectorAccounts: (app) => call('GET', `/connectors/accounts${app ? `?app=${encodeURIComponent(app)}` : ''}`),
  connectToken: (connectorId) => call('POST', '/connectors/connect-token', { connectorId }),
  installConnector: (connectorId) =>
    call('POST', `/connectors/${encodeURIComponent(connectorId)}/install`, {}),
  revokeConnector: (connectorId) => call('DELETE', `/connectors/${encodeURIComponent(connectorId)}`),

  threads: () => call('GET', '/threads'),
  // Marks the conversation seen up to its current activity. The server
  // derives `unread` from this on every list, so there is nothing to keep
  // in step on the client.
  markRead: (id) => call('POST', `/threads/${id}/read`, {}),
  thread: (id) => call('GET', `/threads/${id}`),
  createThread: (t) => call('POST', '/threads', t),
  // `redirectRunId` stops that run in favour of this message; the API starts the
  // new one only once the old has really ended, so two never share a session.
  send: (id, text, opts = {}) => call('POST', `/threads/${id}/messages`, { text, ...opts }),
  patchThread: (id, changes) => call('PATCH', `/threads/${id}`, changes),
  exec: (id, command, agentId = null) => call('POST', `/threads/${id}/exec`, {
    command, ...(agentId ? { agentId } : {}),
  }),
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

  // Run now: the same fire a schedule uses. The key makes a double-click one run.
  runRoutine: (id, key) => call('POST', `/routines/${id}/run`, {},
    key ? { 'idempotency-key': key } : undefined),
  routines: () => call('GET', '/routines'),
  routine: (id) => call('GET', `/routines/${id}`),
  createRoutine: (routine) => call('POST', '/routines', routine),
  updateRoutine: (id, changes) => call('PATCH', `/routines/${id}`, changes),
  // Disables and unschedules. The record survives, because its runs and
  // their evidence point at it.
  archiveRoutine: (id) => call('DELETE', `/routines/${id}`),

  artifacts: () => call('GET', '/artifacts'),

  settings: () => call('GET', '/settings'),
  // Partial: a screen that renders one group cannot reset another it never
  // showed.
  saveSettings: (changes) => call('PUT', '/settings', changes),

  skills: () => call('GET', '/skills'),
  createSkill: (skill) => call('POST', '/skills', skill),
  updateSkill: (id, changes) => call('PATCH', `/skills/${id}`, changes),
  skillVersions: (id) => call('GET', `/skills/${id}/versions`),
  assignSkill: (skillId, agentId, version) =>
    call('POST', `/skills/${skillId}/assignments`, { agentId, version }),
  unassignSkill: (skillId, agentId) =>
    call('DELETE', `/skills/${skillId}/assignments/${agentId}`),

  usage: (agentId, month) =>
    call('GET', `/usage?agentId=${encodeURIComponent(agentId)}${month ? `&month=${month}` : ''}`),

  // The admin governance surface (FEAT-003). Every one of these is gated on the
  // RBAC capability matrix server-side and audited; the client only names the
  // action. The {subject} in a directory path is the TARGET, never the actor --
  // the actor is always the verified token.
  admin: {
    directory: () => call('GET', '/admin/directory'),
    invite: (body) => call('POST', '/admin/directory/invites', body),
    // The typed reason from ConfirmAction rides in the body so the server folds
    // it into the append-only audit `detail`. The {subject}/{agentId} is the
    // TARGET from the path; only the free-text reason is body-supplied.
    suspend: (subject, reason) => call('POST', `/admin/directory/${encodeURIComponent(subject)}/suspend`, { reason }),
    reactivate: (subject, reason) => call('POST', `/admin/directory/${encodeURIComponent(subject)}/reactivate`, { reason }),
    changeRole: (subject, body) => call('PATCH', `/admin/directory/${encodeURIComponent(subject)}`, body),
    killswitch: () => call('GET', '/admin/killswitch'),
    setKillswitch: (body) => call('POST', '/admin/killswitch', body),
    audit: () => call('GET', '/admin/audit'),
    // Put the entrypoint Bot back through onboarding; archive (revoke) a Bot's
    // accumulated memory. Neither ever deletes an audit/evidence row. The
    // operator's reason travels in the body into the audit detail.
    resetOnboarding: (agentId, reason) => call('POST', `/admin/agents/${encodeURIComponent(agentId)}/reset-onboarding`, { reason }),
    archiveMemory: (agentId, reason) => call('POST', `/admin/agents/${encodeURIComponent(agentId)}/archive-memory`, { reason }),
  },
};

// In demo mode the console runs against fixtures instead of the control
// plane. `import.meta.env.DEV` inside demo.js keeps this out of a prod build.
export const api = DEMO ? demoApi : live;
