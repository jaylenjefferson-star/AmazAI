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
    avatar: { shape: 'paper', color: '#2f6fe4' },
    role: 'Repositories, tests, pull requests, application diagnostics.',
    budget: { perMonthUsd: 40, perRunUsd: 2 },
    allowedTools: ['shell', 'file_operations', 'browser'],
    grants: [{ connectorId: 'github', capability: 'repo.write', allowedTools: ['create_pr', 'read_file'] }],
    workspace: { mode: 'project', sessionBytes: 412_000_000 },
    memory: [],
  },
  {
    agentId: 'ops', name: 'Cloud Operations', state: 'active',
    avatar: { shape: 'cloud', color: '#12a594' },
    role: 'AWS investigations, logs, alarms, controlled deployments.',
    budget: { perMonthUsd: 30, perRunUsd: 1.5 },
    allowedTools: ['shell', 'file_operations'],
    grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 84_000_000 },
    memory: [],
  },
  {
    agentId: 'cos', name: 'Chief of Staff', state: 'active',
    avatar: { shape: 'lantern', color: '#8b5cf6' },
    role: 'Intake, prioritization, planning, daily briefings, delegation.',
    budget: { perMonthUsd: 25, perRunUsd: 1 },
    allowedTools: ['file_operations'], grants: [],
    workspace: { mode: 'project', sessionBytes: 21_000_000 }, memory: [],
  },
  {
    agentId: 'res', name: 'Research', state: 'active',
    avatar: { shape: 'moth', color: '#e93d82' },
    role: 'Market and technical research, sourcing, synthesis.',
    budget: { perMonthUsd: 20, perRunUsd: 1 },
    allowedTools: ['browser'], grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 3_100_000 }, memory: [],
  },
  {
    agentId: 'fin', name: 'Finance', state: 'disabled',
    avatar: { shape: 'jelly', color: '#e8833a' },
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
  // The inbox orders on lastActivity, so the fixtures have to carry it or
  // every row sorts on the empty string and the ordering cannot be reviewed.
  { threadId: 'dm-eng', title: 'Engineering',      kind: 'dm', agentIds: ['eng'], lastActivity: iso(-4 * 60_000) },
  { threadId: 'dm-ops', title: 'Cloud Operations', kind: 'dm', agentIds: ['ops'], lastActivity: iso(-38 * 60_000) },
  { threadId: 'dm-cos', title: 'Chief of Staff',   kind: 'dm', agentIds: ['cos'], lastActivity: iso(-3 * 3600_000) },
  { threadId: 'dm-res', title: 'Research',         kind: 'dm', agentIds: ['res'], lastActivity: iso(-26 * 3600_000) },
  { threadId: 'dm-fin', title: 'Finance',          kind: 'dm', agentIds: ['fin'], lastActivity: iso(-3 * 86400_000) },
  { threadId: 'room-ship', title: 'Ship the console', kind: 'room',
    agentIds: ['eng', 'ops', 'cos'], status: 'active',
    lastActivity: iso(-11 * 60_000) },
  // A room is task-bound, so it ends. Without a finished one the read-only
  // state has nothing to render against.
  { threadId: 'room-migrate', title: 'Migrate the evidence bucket', kind: 'room',
    agentIds: ['ops', 'eng'], status: 'completed', readOnly: true,
    createdBy: 'you', lastActivity: iso(-5 * 86400_000) },
];

const ROUTINES = [
  {
    routineId: 'rt_brief', name: 'Morning brief', agentId: 'cos',
    prompt: 'Draft the day from open threads and yesterday\'s activity.',
    trigger: { type: 'schedule', expression: 'cron(30 7 ? * MON-FRI *)' },
    limits: { maxDurationSec: 600 }, threadId: null, enabled: true, status: 'active',
    lastRun: iso(-18 * 3600_000), timezone: 'America/Los_Angeles',
    createdAt: iso(-30 * 86400_000), updatedAt: iso(-18 * 3600_000),
  },
  {
    routineId: 'rt_alarms', name: 'Overnight alarm sweep', agentId: 'ops',
    prompt: 'Check overnight alarms. Open a room with Engineering if anything needs attention.',
    trigger: { type: 'schedule', expression: 'cron(0 2 * * ? *)' },
    limits: { maxDurationSec: 900 }, threadId: null, enabled: true, status: 'active',
    lastRun: iso(-6 * 3600_000), timezone: 'America/Los_Angeles',
    createdAt: iso(-20 * 86400_000), updatedAt: iso(-6 * 3600_000),
  },
  {
    routineId: 'rt_ledger', name: 'Weekly ledger close', agentId: 'fin',
    prompt: 'Reconcile the week\'s spend against budget and flag anything over.',
    trigger: { type: 'schedule', expression: 'rate(7 days)' },
    limits: { maxDurationSec: 600 }, threadId: null, enabled: false, status: 'active',
    lastRun: null, timezone: 'America/Los_Angeles',
    createdAt: iso(-9 * 86400_000), updatedAt: iso(-2 * 86400_000),
  },
];

const ARTIFACTS = [
  { runId: 'run_9a22', agentId: 'eng', threadId: 't-deploy',
    goal: 'Ship the console to CloudFront',
    outcome: 'completed', summary: 'Synced 14 objects and invalidated the cache.',
    evidenceKey: 'runs/run_9a22/manifest.json',
    startedAt: iso(-2 * 3600_000 - 6 * 60_000), endedAt: iso(-2 * 3600_000), costUsd: 0.128 },
  { runId: 'run_5xx', agentId: 'ops', threadId: 't-alarm',
    goal: 'Investigate the 5xx spike',
    outcome: 'completed',
    summary: 'Root cause was a stale cache entry from the previous deploy. Documented in the report.',
    evidenceKey: 'runs/run_5xx/manifest.json',
    startedAt: iso(-26 * 3600_000 - 40 * 60_000), endedAt: iso(-26 * 3600_000), costUsd: 0.41 },
  { runId: 'run_fail1', agentId: 'res', threadId: null,
    goal: 'Source vendors for the Q3 renewal',
    outcome: 'failed', summary: 'Stopped after the browser connector hit its rate limit twice.',
    evidenceKey: 'runs/run_fail1/manifest.json',
    startedAt: iso(-4 * 86400_000 - 12 * 60_000), endedAt: iso(-4 * 86400_000), costUsd: 0.06 },
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
  // The inbox opens dm threads, so those are the ones that have to carry a
  // conversation -- an empty timeline reviews nothing.
  'dm-eng': [
    { role: 'user', author: 'you', text: 'Why did last night\u2019s deploy roll back?' },
    { role: 'assistant', author: 'Engineering', text: 'The parity check failed on two response contracts. Neither invariant actually broke \u2014 the patterns matched the field tables by literal text, and the tables had been reformatted.' },
    { role: 'user', author: 'you', text: 'Can you fix it without widening the change?' },
    { role: 'assistant', author: 'Engineering', text: 'Yes. Two patterns, matched on the binding rather than the formatting. I will need approval before anything touches production.' },
  ],
  'dm-ops': [
    { role: 'user', author: 'you', text: 'Anything from the overnight alarms?' },
    { role: 'assistant', author: 'Cloud Operations', text: 'Two 5xx spikes, both from the same deploy, both cleared on rollback. Nothing outstanding.' },
  ],
  'dm-cos': [], 'dm-res': [], 'dm-fin': [],
  'room-ship': [
    { role: 'user', author: 'you', text: 'Where are we for the release?' },
    { role: 'assistant', author: 'Engineering', text: 'Tests are green on the branch. The full job ran end to end for the first time.' },
    { role: 'assistant', author: 'Cloud Operations', text: 'Distribution is warm and the alarm thresholds are back to normal.' },
  ],
  'room-migrate': [
    { role: 'user', author: 'you', text: 'Move the sealed bundles to the new bucket. Nothing may be rewritten.' },
    { role: 'assistant', author: 'Cloud Operations', text: 'Copied 1,284 objects and verified every checksum against the manifest. Originals left in place.' },
  ],
};

//: Owner preferences. Only what was changed, as the stored row holds.
const SETTINGS = { notifications: {} };

//: Read markers, by thread. Empty to start, so a fresh demo session opens on
//: an inbox with everything unread -- which is the state the design is for.
const READ = {};

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

/* The vocabulary the real API serves from services/amazai/agents.py. Kept
   here only so the form can be driven without a deployed control plane; the
   shipped console reads it from GET /agents/options. */
const OPTIONS = {
  // The character archetypes, matching agents.AVATAR_SHAPES. These were the
  // old geometric names, which no longer exist on either side.
  shapes: ['pebble', 'paper', 'jelly', 'cloud', 'lantern', 'moth'],
  colors: ['#e5484d', '#e8833a', '#f0a93b', '#3dc98a', '#12a594',
           '#2f6fe4', '#8b5cf6', '#e93d82', '#8b6c4e', '#8a909c'],
  workingStyles: ['autonomous', 'collaborative', 'advisory'],
  modelTiers: [
    { key: 'frontier', ladder: ['claude-opus-5', 'claude-opus-4-8', 'claude-sonnet-5'],
      maxTokens: 32000, effort: 'high' },
    { key: 'balanced', ladder: ['claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5'],
      maxTokens: 16000, effort: 'medium' },
    { key: 'fast', ladder: ['claude-haiku-4-5', 'claude-sonnet-5'],
      maxTokens: 8000, effort: 'low' },
  ],
  defaultModelTier: 'balanced',
  limits: { maxAgents: 25, maxConcurrentRuns: 8, maxMonthlyUsd: 500 },
  // Empty, exactly as a fresh organization is: no connector installed means
  // no grant is offerable, which is the state the safe default comes from.
  connectors: [],
};

export const demoApi = {
  agents: async () => (await wait(120), { agents: AGENTS }),
  agentOptions: async () => (await wait(90), OPTIONS),
  createAgent: async (agent) => {
    await wait(400);
    const agentId = agent.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const created = {
      ...agent, agentId, status: 'active', state: 'active',
      accent: agent.avatar.color,
      workspace: { mode: 'ephemeral', sessionBytes: 0 },
      grants: [], memory: [],
    };
    AGENTS.push(created);
    THREADS.push({ threadId: `dm-${agentId}`, title: agent.name,
                   kind: 'dm', agentIds: [agentId] });
    MESSAGES[`dm-${agentId}`] = [];
    return created;
  },
  archiveAgent: async (id) => {
    await wait(150);
    const agent = AGENTS.find((a) => a.agentId === id);
    if (agent) { agent.status = 'archived'; agent.state = 'offline'; }
    return { ...(agent || {}) };
  },
  agent: async (id) => (await wait(80), AGENTS.find((a) => a.agentId === id) || AGENTS[0]),
  updateAgent: async (id, changes) => {
    await wait(200);
    const agent = AGENTS.find((a) => a.agentId === id);
    if (!agent) throw new Error('No such agent');
    // Applied rather than acknowledged. Returning {} was a fake success --
    // the settings screen renders what comes back, so a save would have
    // blanked the companion it had just written.
    const { avatar, budget, modelTier, ...rest } = changes;
    Object.assign(agent, rest);
    if (avatar) agent.avatar = { ...agent.avatar, ...avatar };
    if (budget) agent.budget = { ...agent.budget, ...budget };
    if (modelTier) agent.model = { ...(agent.model || {}), tier: modelTier };
    return { ...agent };
  },
  addMemory: async () => ({}),
  deleteMemory: async () => ({}),

  // `unread` is derived by the control plane from lastActivity against the
  // read marker. Mirrored here rather than stored as a flag, so the fixture
  // cannot drift into showing an unread row that the real API would not.
  threads: async () => (await wait(120), {
    threads: THREADS.map((t) => ({
      ...t,
      readAt: READ[t.threadId] || null,
      unread: Boolean(t.lastActivity && t.lastActivity > (READ[t.threadId] || '')),
    })),
  }),
  // The whole record plus its messages, as GET /threads/{id} returns. The
  // stub this used to be -- id and messages only -- meant a room rendered
  // with no participants, no title and no status, so the read-only state and
  // the participant marks could not be reviewed at all.
  thread: async (id) => {
    await wait(80);
    const thread = THREADS.find((t) => t.threadId === id) || { threadId: id };
    return { ...thread, threadId: id, messages: MESSAGES[id] || [] };
  },
  markRead: async (id) => {
    const thread = THREADS.find((t) => t.threadId === id);
    READ[id] = thread?.lastActivity || iso();
    return { threadId: id, readAt: READ[id] };
  },
  createThread: async (t) => {
    const threadId = `room-${Math.random().toString(36).slice(2, 8)}`;
    const thread = { threadId, kind: t?.kind || 'room', title: t?.title || 'New room',
                      agentIds: t?.agentIds || [], status: 'active', createdBy: 'you',
                      lastActivity: new Date().toISOString() };
    THREADS.push(thread);
    MESSAGES[threadId] = [];
    return thread;
  },
  send: async () => (await wait(200), { runId: 'run-9a22' }),
  // Agent-to-agent traffic bound to a room. Read-only in the console, and
  // the reason the room keeps it in its own feed rather than the chat.
  // Owner preferences, defaulted on read exactly as the control plane does,
  // so the sheet can be reviewed without a deploy.
  settings: async () => {
    await wait(80);
    return {
      notifications: { completion: true, inputNeeded: true, failure: true, ...SETTINGS.notifications },
      theme: SETTINGS.theme ?? 'system',
      defaultTimezone: SETTINGS.defaultTimezone ?? null,
      workspaceName: SETTINGS.workspaceName ?? null,
      // The fixture account is already set up, so ?demo=1 opens on the inbox
      // rather than on first-run setup. Clear it to review onboarding.
      onboardedAt: SETTINGS.onboardedAt ?? iso(-30 * 24 * 60 * 60_000),
      updatedAt: SETTINGS.updatedAt ?? null,
    };
  },
  saveSettings: async (changes) => {
    await wait(150);
    // Refused by name, as the API does: an approval is a question a run
    // cannot proceed without, not a notification.
    const kinds = Object.keys(changes.notifications || {});
    const bad = kinds.filter((k) => !['completion', 'inputNeeded', 'failure'].includes(k));
    if (bad.length) throw new Error(`not a notification kind: ${bad.join(', ')}`);

    if (changes.notifications) {
      SETTINGS.notifications = { ...SETTINGS.notifications, ...changes.notifications };
    }
    if ('theme' in changes) SETTINGS.theme = changes.theme;
    if ('defaultTimezone' in changes) SETTINGS.defaultTimezone = changes.defaultTimezone;
    if ('workspaceName' in changes) SETTINGS.workspaceName = changes.workspaceName;
    // Asserted, never supplied as a time -- the same contract as the API, so
    // a console reviewed here behaves the way it will against the real one.
    if ('onboarded' in changes) {
      if (changes.onboarded !== true) throw new Error('onboarded is asserted by finishing setup and is not unset here');
      SETTINGS.onboardedAt = SETTINGS.onboardedAt ?? iso();
    }
    SETTINGS.updatedAt = iso();
    return demoApi.settings();
  },

  coordination: async (id) => {
    await wait(80);
    if (id !== 'room-ship') return { coordination: [] };
    return { coordination: [
      { kind: 'handoff', at: iso(-16 * 60_000), fromAgentId: 'cos', toAgentId: 'eng',
        status: 'accepted', summary: 'Cut the release once CI is green.' },
      { kind: 'message', at: iso(-14 * 60_000), fromAgentId: 'eng', toAgentId: 'ops',
        status: 'delivered', priority: 'normal',
        summary: 'Invalidation will be needed once the bundle hash changes.' },
      { kind: 'handoff', at: iso(-12 * 60_000), fromAgentId: 'eng', toAgentId: 'ops',
        status: 'accepted', summary: 'Warm the distribution before the cutover.' },
    ] };
  },
  exec: async (_id, command) => (await wait(260), {
    stdout: command.startsWith('ls')
      ? 'dist/\nindex.html\nassets/\npackage.json'
      : `demo: '${command}' was not executed — no cloud computer is attached`,
    stderr: '',
  }),

  run: async () => (await wait(120), { state: 'completed', approvals: [] }),
  cancel: async () => ({}),
  decide: async () => (await wait(200), {}),
  approvals: async () => (await wait(80), { approvals: [APPROVAL] }),

  // Routines and artifacts, kept to the shape `amazai/routines.py` and the
  // `/artifacts` route actually return. Both screens went un-reviewable the
  // day they were wired to real routes, because this file had no matching
  // stub -- calling `api.routines()` under `?demo=1` would have thrown.
  routines: async () => (await wait(100), {
    routines: [...ROUTINES].sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
  }),
  routine: async (id) => (await wait(80), ROUTINES.find((r) => r.routineId === id)),
  createRoutine: async (body) => {
    await wait(300);
    const name = (body.name || '').trim();
    if (name.length < 2 || name.length > 80) throw new Error('name must be 2-80 characters');
    const agent = AGENTS.find((a) => a.agentId === body.agentId);
    if (!agent) throw new Error('agentId is required');
    const prompt = (body.prompt || '').trim();
    if (prompt.length < 2) throw new Error('prompt must be 2-8000 characters');
    const trigger = body.trigger || { type: 'manual' };
    if (trigger.type === 'schedule' && !trigger.expression) {
      throw new Error('a schedule trigger needs trigger.expression');
    }
    const routine = {
      routineId: `rt_${Math.random().toString(36).slice(2, 8)}`,
      name, agentId: body.agentId, prompt, trigger,
      limits: { maxDurationSec: body.limits?.maxDurationSec || 600 },
      threadId: null, enabled: body.enabled !== false, status: 'active', lastRun: null,
      timezone: agent.timezone || null,
      createdAt: iso(), updatedAt: iso(),
    };
    ROUTINES.push(routine);
    return routine;
  },
  updateRoutine: async (id, changes) => {
    await wait(200);
    const routine = ROUTINES.find((r) => r.routineId === id);
    if (!routine) throw new Error('No such routine');
    Object.assign(routine, changes, { updatedAt: iso() });
    return { ...routine };
  },
  archiveRoutine: async (id) => {
    await wait(150);
    const routine = ROUTINES.find((r) => r.routineId === id);
    if (routine) Object.assign(routine, { enabled: false, status: 'archived', updatedAt: iso() });
    return { ...(routine || {}) };
  },

  artifacts: async () => (await wait(100), {
    artifacts: [...ARTIFACTS].sort((a, b) => (b.endedAt || '').localeCompare(a.endedAt || '')),
  }),

  skills: async () => (await wait(80), { skills: [] }),
  skillVersions: async () => (await wait(60), { versions: [] }),
  assignSkill: async () => ({}),
  unassignSkill: async () => ({}),

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
