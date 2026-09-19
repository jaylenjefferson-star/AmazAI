/*
 * Dev-only fixture backend.
 *
 * The console cannot render anything real until the stack is deployed and
 * seats are provisioned, which makes the UI unreviewable exactly when it is
 * most worth reviewing. `npm run dev -- --open '/?demo=1'` renders the whole
 * surface against fixtures instead.
 *
 * Guarded by import.meta.env.DEV, so none of this survives `npm run build`.
 * It never talks to AWS and never holds a credential.
 */

export const DEMO = import.meta.env.DEV
  && typeof location !== 'undefined'
  && new URLSearchParams(location.search).has('demo');

const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();

const AGENTS = [
  {
    agentId: 'eng', name: 'Engineering', state: 'active',
    role: 'Repositories, tests, pull requests, application diagnostics.',
    budget: { perMonthUsd: 40, perRunUsd: 2 },
    allowedTools: ['shell', 'file_operations', 'browser'],
    grants: [{ connectorId: 'github', capability: 'repo.write', allowedTools: ['create_pr', 'read_file'] }],
    workspace: { mode: 'project', sessionBytes: 412_000_000 },
    memory: [],
  },
  {
    agentId: 'ops', name: 'Cloud Operations', state: 'active',
    role: 'AWS investigations, logs, alarms, controlled deployments.',
    budget: { perMonthUsd: 30, perRunUsd: 1.5 },
    allowedTools: ['shell', 'file_operations'],
    grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 84_000_000 },
    memory: [],
  },
  {
    agentId: 'cos', name: 'Chief of Staff', state: 'active',
    role: 'Intake, prioritization, planning, daily briefings, delegation.',
    budget: { perMonthUsd: 25, perRunUsd: 1 },
    allowedTools: ['file_operations'], grants: [],
    workspace: { mode: 'project', sessionBytes: 21_000_000 }, memory: [],
  },
  {
    agentId: 'res', name: 'Research', state: 'active',
    role: 'Market and technical research, sourcing, synthesis.',
    budget: { perMonthUsd: 20, perRunUsd: 1 },
    allowedTools: ['browser'], grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 3_100_000 }, memory: [],
  },
  {
    agentId: 'fin', name: 'Finance', state: 'disabled',
    role: 'Ledger reconciliation and spend reporting.',
    budget: { perMonthUsd: 15, perRunUsd: 0.5 },
    allowedTools: [], grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 0 }, memory: [],
  },
];

const THREADS = [
  { threadId: 't-deploy', title: 'Ship the console to CloudFront', kind: 'task', agentIds: ['eng'] },
  { threadId: 't-alarm',  title: 'Investigate the 5xx spike', kind: 'task', agentIds: ['ops'] },
  { threadId: 't-brief',  title: 'Monday briefing', kind: 'task', agentIds: ['cos'] },
];

const MESSAGES = {
  't-deploy': [
    { role: 'user', author: 'you', text: 'Build the console and push it to the CloudFront distribution. Tell me before anything touches production.' },
    { role: 'assistant', author: 'Engineering', text: 'Building web/ now. I will need approval before the invalidation, since that is user-visible immediately.' },
  ],
  't-alarm': [
    { role: 'user', author: 'you', text: 'The 5xx alarm fired twice overnight. What happened?' },
  ],
  't-brief': [],
};

const APPROVAL = {
  approvalId: 'apv-7c41', runId: 'run-9a22', agentId: 'eng',
  action: 'cloudfront.create_invalidation',
  risk: 'high', reversible: false, status: 'pending',
  requestedAt: iso(-90_000), expiresAt: iso(8 * 60_000),
  arguments: { distributionId: 'EXAMPLE00000001', paths: '/*' },
  target: { account: '123456789012', env: 'production', region: 'us-west-2' },
  why: 'The console bundle hash changed, so cached index.html would keep serving the previous build.',
  requestedBy: { agentId: 'eng' },
};

/* ---------------------------------------------------------------- the api */

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

export const demoApi = {
  agents: async () => (await wait(120), { agents: AGENTS }),
  agent: async (id) => (await wait(80), AGENTS.find((a) => a.agentId === id) || AGENTS[0]),
  updateAgent: async () => ({}),
  addMemory: async () => ({}),
  deleteMemory: async () => ({}),

  threads: async () => (await wait(120), { threads: THREADS }),
  thread: async (id) => (await wait(80), { threadId: id, messages: MESSAGES[id] || [] }),
  createThread: async () => ({}),
  send: async () => (await wait(200), { runId: 'run-9a22' }),
  exec: async (_id, command) => (await wait(260), {
    stdout: command.startsWith('ls')
      ? 'dist/\nindex.html\nassets/\npackage.json'
      : `demo: '${command}' was not executed — no cloud computer is attached`,
    stderr: '',
  }),

  run: async () => ({}),
  cancel: async () => ({}),
  decide: async () => (await wait(200), {}),

  usage: async (agentId) => ({
    agentId, month: iso().slice(0, 7),
    totalUsd: { eng: 11.42, ops: 4.06, cos: 1.88, res: 0.71, fin: 0 }[agentId] ?? 0,
    runs: [],
  }),
};

/* ------------------------------------------------------------ the socket */

/**
 * Replays one run: a little streamed prose, a couple of tool calls, then the
 * approval that is the reason this product exists. Enough to judge the real
 * thing by.
 */
export function demoConnect(onEvent, onStatus) {
  let dead = false;
  const timers = [];
  const at = (ms, fn) => timers.push(setTimeout(() => { if (!dead) fn(); }, ms));

  at(300, () => onStatus?.('connected'));

  const script = [
    [1400, { type: 'tool', threadId: 't-deploy', name: 'shell', summary: 'npm run build  →  built in 4.21s' }],
    [2200, { type: 'tool', threadId: 't-deploy', name: 'file_operations', summary: 'sync dist/ → s3://amazai-console-example (14 objects)' }],
    [2700, { type: 'handoff', threadId: 't-deploy', handoff: {
      handoffId: 'hoff_31ab', fromAgentId: 'eng', toAgentId: 'ops', status: 'proposed',
      goal: 'Confirm the distribution is serving the new bundle once the invalidation clears.',
      constraints: ['Read-only: no stack changes', 'Stop and report if the 5xx rate moves'],
      grantsOffered: [],
    } }],
    [3000, { type: 'run.state', threadId: 't-deploy', runId: 'run-9a22', costUsd: 0.128 }],
  ];
  script.forEach(([ms, ev]) => at(ms, () => onEvent(ev)));

  const prose = 'Build is clean and the bundle is on S3. The distribution is still serving the '
    + 'previous index.html from cache, so the last step is an invalidation — which is immediate '
    + 'and irreversible, so it needs you.';
  prose.split(/(?<= )/).forEach((word, i) => {
    at(3600 + i * 34, () => onEvent({ type: 'delta', threadId: 't-deploy', text: word }));
  });

  at(3600 + prose.split(/(?<= )/).length * 34 + 400, () => {
    onEvent({ type: 'approval.requested', threadId: 't-deploy', approval: APPROVAL });
  });

  return {
    send: () => {},
    close: () => { dead = true; timers.forEach(clearTimeout); },
  };
}
