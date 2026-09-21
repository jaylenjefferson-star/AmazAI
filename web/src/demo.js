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

import { describeSchedule } from './schedules';

export const DEMO = import.meta.env.DEV
  && typeof location !== 'undefined'
  && new URLSearchParams(location.search).has('demo');

const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();

/*
 * Three shapes of account, because the bug this exists to review is a matter of
 * which one you are in:
 *
 *   ?demo=1            a working org, first Bot included
 *   ?demo=1&fresh=1    nobody here yet -- first-run setup
 *   ?demo=1&offer=1    a fresh deploy: only the Engineering seat, no first Bot,
 *                      never onboarded. Before the fix this account skipped
 *                      setup and its first conversation was /agents/eng.
 */
// Read only in a dev build. `import.meta.env.DEV` is replaced by `false` at build
// time, which folds this whole expression to a constant -- a bare
// `new URLSearchParams(...)` here would be a side effect the bundler cannot
// prove away, and everything it touches would ship.
const MODE = !import.meta.env.DEV || typeof location === 'undefined' ? 'full'
  : new URLSearchParams(location.search).has('fresh') ? 'fresh'
    : new URLSearchParams(location.search).has('offer') ? 'offer' : 'full';

// What the API writes as a Bot's first message (services/amazai/onboarding.py).
// Mirrored, not imported: this file must stay free of anything a production
// bundle would carry.
const CLOSEST_FITS = ['Managing my inbox', 'Managing my calendar',
                      'Planning my day and week', 'Writing and drafting'];
const greeting = (author, { entrypoint = false, operator = '', at = iso() } = {}) => ({
  role: 'assistant', author, starter: true, at,
  text: `${operator ? `Hey ${operator} \u2014 good to meet you.` : 'Hey \u2014 good to meet you.'}`
    + '\n\nWhat do you mainly want me for?'
    + (entrypoint ? '\n\nPick the closest fit, or type your own.' : ''),
  ...(entrypoint ? { suggestions: CLOSEST_FITS } : {}),
});

const AGENTS = [
  {
    agentId: 'eng', name: 'Engineering', state: 'active', title: 'Code',
    avatar: { shape: 'paper', color: '#2f6fe4' },
    role: 'Repositories, tests, pull requests, application diagnostics.',
    budget: { perMonthUsd: 40, perRunUsd: 2 },
    allowedTools: ['shell', 'file_operations', 'browser'],
    grants: [{ connectorId: 'github', capability: 'repo.write', allowedTools: ['create_pr', 'read_file'] }],
    workspace: { mode: 'project', sessionBytes: 412_000_000 },
    memory: [],
  },
  {
    agentId: 'ops', name: 'Cloud Operations', state: 'active', title: 'AWS', reportsTo: 'eng',
    avatar: { shape: 'cloud', color: '#12a594' },
    role: 'AWS investigations, logs, alarms, controlled deployments.',
    budget: { perMonthUsd: 30, perRunUsd: 1.5 },
    allowedTools: ['shell', 'file_operations'],
    grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 84_000_000 },
    memory: [],
  },
  {
    // The first Bot. Its thread is empty but for its greeting, which is the
    // state worth reviewing.
    agentId: 'cos', name: 'Chief', state: 'active', title: 'Chief', entrypoint: true,
    avatar: { shape: 'pebble', color: '#8b5cf6' },
    role: 'Your first Bot. Finds out what you need most, takes the first real task, connects your tools as it needs them, and ships the result.',
    budget: { perMonthUsd: 25, perRunUsd: 1 },
    allowedTools: ['file_operations'], grants: [],
    workspace: { mode: 'project', sessionBytes: 21_000_000 },
    memory: [
      { memId: 'mem_bullets', title: 'Prefers bullets', body: 'Summaries as short bullets, not paragraphs.',
        kind: 'foundational', scope: 'agent', source: 'agent', pinned: true, status: 'published', usedCount: 4 },
      { memId: 'mem_tz', title: 'Works Pacific time', body: 'Schedule everything in America/Los_Angeles.',
        kind: 'note', scope: 'agent', source: 'user', pinned: false, status: 'published', usedCount: 1 },
    ],
    skillAssignments: [{ skillId: 'weekly-plan', version: 1 }],
  },
  {
    agentId: 'res', name: 'Research', state: 'active', title: 'Sourcing',
    avatar: { shape: 'moth', color: '#e93d82' },
    role: 'Market and technical research, sourcing, synthesis.',
    budget: { perMonthUsd: 20, perRunUsd: 1 },
    allowedTools: ['browser'], grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 3_100_000 }, memory: [],
  },
  {
    agentId: 'fin', name: 'Finance', state: 'disabled', title: 'Ledger',
    avatar: { shape: 'jelly', color: '#e8833a' },
    role: 'Ledger reconciliation and spend reporting.',
    budget: { perMonthUsd: 15, perRunUsd: 0.5 },
    allowedTools: [], grants: [],
    workspace: { mode: 'ephemeral', sessionBytes: 0 }, memory: [],
  },
];

// Who reports to whom, by the server's rule (services/amazai/org.py): the line a person
// chose if it names a live Bot (or "owner"), otherwise Chief. Nothing is stored for the
// default, so a Bot made later lands in the right place. Same refusals, same words.
const GONE = ['archived', 'failed'];
const liveBots = () => AGENTS.filter((a) => !GONE.includes(a.status || a.state));
function managerOf(agent) {
  const live = liveBots();
  const stored = agent.reportsTo;
  if (stored === 'owner') return null;
  if (stored && stored !== agent.agentId && live.some((a) => a.agentId === stored)) return stored;
  const chief = live.find((a) => a.entrypoint);
  return chief && chief.agentId !== agent.agentId ? chief.agentId : null;
}
const withManager = (a) => ({ ...a, managerId: managerOf(a) });
function checkReportsTo(agentId, target) {
  if (target === 'owner') return 'owner';
  if (typeof target !== 'string' || !target) throw new Error('reportsTo must be a Bot id, or "owner"');
  if (target === agentId) throw new Error('a Bot cannot report to itself');
  if (!liveBots().some((a) => a.agentId === target)) throw new Error(`'${target}' is not an active Bot`);
  for (let cursor = target; cursor; cursor = managerOf(AGENTS.find((a) => a.agentId === cursor) || {})) {
    if (agentId && cursor === agentId) {
      throw new Error(`'${target}' already reports up to '${agentId}'; that would make a loop`);
    }
  }
  return target;
}

const THREADS = [
  { threadId: 't-deploy', title: 'Ship the console to CloudFront', kind: 'task', agentIds: ['eng'] },
  { threadId: 't-alarm',  title: 'Investigate the 5xx spike', kind: 'task', agentIds: ['ops'] },
  { threadId: 't-brief',  title: 'Monday briefing', kind: 'task', agentIds: ['cos'] },
  // The inbox orders on lastActivity, so the fixtures have to carry it or
  // every row sorts on the empty string and the ordering cannot be reviewed.
  { threadId: 'dm-eng', title: 'Engineering',      kind: 'dm', agentIds: ['eng'], lastActivity: iso(-4 * 60_000) },
  { threadId: 'dm-ops', title: 'Cloud Operations', kind: 'dm', agentIds: ['ops'], lastActivity: iso(-38 * 60_000) },
  { threadId: 'dm-cos', title: 'Chief',            kind: 'dm', agentIds: ['cos'], lastActivity: iso(-3 * 3600_000) },
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
  { artifactId: 'evidence/run_9a22/artifacts/launch-checklist.md', runId: 'run_9a22', agentId: 'eng',
    name: 'launch-checklist.md', sizeBytes: 4821, updatedAt: iso(-2 * 3600_000), downloadUrl: '#' },
  { artifactId: 'evidence/run_5xx/artifacts/incident-report.md', runId: 'run_5xx', agentId: 'ops',
    name: 'incident-report.md', sizeBytes: 12334, updatedAt: iso(-26 * 3600_000), downloadUrl: '#' },
  { artifactId: 'evidence/run_5xx/artifacts/cache-timeline.csv', runId: 'run_5xx', agentId: 'ops',
    name: 'cache-timeline.csv', sizeBytes: 944, updatedAt: iso(-26 * 3600_000 - 40 * 60_000), downloadUrl: '#' },
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
  'dm-cos': [greeting('Chief', { entrypoint: true, operator: 'Jaylen', at: iso(-3 * 3600_000) })],
  'dm-res': [], 'dm-fin': [],
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
  // A write with nothing on the floor and no pre-approved rule: `policy.default`.
  policy: { rule: 'default', matched: 'write', reason: 'write capability without a pre-approved rule' },
};

// The first Bot's answer to each closest fit: one concrete first move, one
// question at most, and no claim to a tool it has not been given.
const FIRST_STEPS = {
  'Managing my inbox': {
    text: 'Good place to start. I can’t see your mail yet, so I’ve set up a workspace for it and I’ll pick up the moment it’s connected. While you do: what should I clear first, newsletters, receipts, or anything from a specific person?',
    steps: [
      { name: 'connectors', summary: 'Checking which mail account is connected' },
      { name: 'file_operations', summary: 'Setting up /workspace/inbox' },
    ],
    cards: [{ type: 'connect', name: 'Gmail', why: 'To triage your inbox I need read access to your mail.' }],
  },
  'Managing my calendar': {
    text: 'Good place to start. Once I can see your calendar I’ll fit things around what’s booked. Meanwhile, tell me what a good week looks like for you and I’ll build around it.',
    steps: [
      { name: 'connectors', summary: 'Checking which calendar is connected' },
    ],
    cards: [{ type: 'connect', name: 'Google Calendar', why: 'To see what is already booked I need read access to your calendar.' }],
  },
  // The whole first win: a result, and then what to do so it keeps happening.
  'Planning my day and week': {
    text: 'Here’s a first pass at your week: mornings held for deep work, meetings batched after lunch, Friday left open. I’ve saved it so you can edit it.\n\nIf that’s useful, I can draft it again every weekday morning, and a Calendar Bot could own the scheduling side from here.',
    steps: [
      { name: 'memory', summary: 'Reading what I know about you' },
      { name: 'file_operations', summary: 'Drafting this week’s plan' },
      { name: 'file_operations', summary: 'Saving plan.md (1 page)' },
    ],
    cards: [
      // Demo-only: no run can produce a file card yet (nothing hands one to the
      // console), so this exists to review the renderer, not the runtime.
      { type: 'file', name: 'This week — plan.md', meta: '1 page · saved to your artifacts' },
      { type: 'routine', name: 'Weekday planning', preset: 'weekday-9',
        prompt: 'Draft the day from my open threads and yesterday’s activity.' },
    ],
  },
  'Writing and drafting': {
    text: 'Good place to start. Paste in something you’re working on, or tell me what you need written and who it’s for, and I’ll draft it. Nothing goes out until you’ve read it.',
    steps: [],
    cards: [],
  },
};

// Guarded for the same reason as MODE: this mutates the fixtures, so unguarded
// it would keep every one of them in a production bundle.
if (import.meta.env.DEV && MODE !== 'full') {
  const keep = new Set(MODE === 'offer' ? ['eng'] : []);
  const only = (list, ok) => list.splice(0, list.length, ...list.filter(ok));
  only(AGENTS, (a) => keep.has(a.agentId));
  only(THREADS, (t) => t.agentIds.length > 0 && t.agentIds.every((id) => keep.has(id)));
  only(ROUTINES, (r) => keep.has(r.agentId));
  only(ARTIFACTS, (a) => keep.has(a.agentId));
}

/* ---------------------------------------------------------------- the api */

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Scripted runs, by id, and the socket to narrate them on while one is open.
const RUNS = {};
let liveEmit = null;

// The API writes the last message onto the thread on every send and every
// reply. Derived from MESSAGES here so the fixture cannot drift into a preview
// that disagrees with the conversation it heads.
function previewFor(threadId) {
  const last = [...(MESSAGES[threadId] || [])].reverse().find((m) => !m.kind && m.text);
  if (!last?.text) return {};
  return {
    preview: last.text.replace(/\s+/g, ' ').slice(0, 140),
    previewRole: last.role === 'user' ? 'user' : 'assistant',
  };
}

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

// A real account keeps its theme across a reload; the demo's in-memory settings do not, so it
// remembers the choice the way a server would.
const demoTheme = () => { try { return localStorage.getItem('amazai.demo.theme'); } catch { return null; } };

export const demoApi = {
  agents: async () => (await wait(120), { agents: liveBots().map(withManager) }),
  agentOptions: async () => (await wait(90), OPTIONS),
  createAgent: async (agent) => {
    await wait(400);
    // The API's own rule and its own words, so the failure a second first Bot
    // produces can be reviewed here too.
    if (agent.entrypoint && AGENTS.some((a) => a.entrypoint)) {
      throw new Error('this organization already has a first Bot');
    }
    const agentId = agent.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const { operatorName, reportsTo, ...profile } = agent;
    const created = {
      ...profile, agentId, status: 'active', state: 'active',
      ...(reportsTo ? { reportsTo: checkReportsTo('', reportsTo) } : {}),
      title: profile.title || (profile.entrypoint ? 'Chief' : ''),
      role: profile.role || (profile.entrypoint
        ? 'Your first Bot. Finds out what you need most, takes the first real task, connects your tools as it needs them, and ships the result.'
        : ''),
      entrypoint: Boolean(profile.entrypoint),
      accent: agent.avatar.color,
      workspace: { mode: 'ephemeral', sessionBytes: 0 },
      grants: [], memory: [],
    };
    AGENTS.push(created);
    // Every new Bot greets, as the API writes it: same transaction as the
    // thread, so a Bot never exists without its first message.
    THREADS.push({ threadId: `dm-${agentId}`, title: agent.name, kind: 'dm',
                   agentIds: [agentId], lastActivity: iso() });
    MESSAGES[`dm-${agentId}`] = [
      greeting(agent.name, { entrypoint: created.entrypoint, operator: operatorName || '', at: iso() }),
    ];
    return withManager(created);
  },
  archiveAgent: async (id) => {
    await wait(150);
    const agent = AGENTS.find((a) => a.agentId === id);
    if (agent) { agent.status = 'archived'; agent.state = 'offline'; }
    return { ...(agent || {}) };
  },
  agent: async (id) => (await wait(80), withManager(AGENTS.find((a) => a.agentId === id) || AGENTS[0])),
  updateAgent: async (id, changes) => {
    await wait(200);
    const agent = AGENTS.find((a) => a.agentId === id);
    if (!agent) throw new Error('No such agent');
    // Applied rather than acknowledged. Returning {} was a fake success --
    // the settings screen renders what comes back, so a save would have
    // blanked the companion it had just written.
    const { avatar, budget, modelTier, reportsTo, ...rest } = changes;
    Object.assign(agent, rest);
    if (reportsTo !== undefined) agent.reportsTo = checkReportsTo(id, reportsTo);
    if (avatar) agent.avatar = { ...agent.avatar, ...avatar };
    if (budget) agent.budget = { ...agent.budget, ...budget };
    if (modelTier) agent.model = { ...(agent.model || {}), tier: modelTier };
    return withManager(agent);
  },
  setGrant: async (agentId, connectorId, body) => {
    await wait(120);
    const agent = AGENTS.find((a) => a.agentId === agentId);
    if (!agent) throw new Error('No such agent');
    const row = { connectorId, capability: body.capability || 'admin', allowedTools: body.allowedTools || ['*'] };
    agent.grants = [...(agent.grants || []).filter((g) => g.connectorId !== connectorId), row];
    return row;
  },
  removeGrant: async (agentId, connectorId) => {
    await wait(120);
    const agent = AGENTS.find((a) => a.agentId === agentId);
    if (agent) agent.grants = (agent.grants || []).filter((g) => g.connectorId !== connectorId);
    return { agentId, connectorId, removed: true };
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
      ...previewFor(t.threadId),
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
  // A run is scripted end to end so the whole first win can be reviewed: the
  // message lands, the run's steps stream in over the socket (the roster and the
  // header change with them), and only then does the reply appear -- in the
  // order the real system would produce them. Nothing actually runs.
  send: async (threadId, text) => {
    await wait(200);
    const thread = THREADS.find((t) => t.threadId === threadId);
    const agent = AGENTS.find((a) => thread?.agentIds?.includes(a.agentId));
    const list = (MESSAGES[threadId] ||= []);
    list.push({ role: 'user', author: 'you', text, at: iso() });

    const script = FIRST_STEPS[text];
    const steps = script?.steps || [];
    const runId = `run-demo-${Math.random().toString(36).slice(2, 8)}`;
    const duration = steps.length ? 700 + steps.length * 1000 : 500;
    RUNS[runId] = { doneAt: Date.now() + duration };

    if (liveEmit && steps.length) {
      liveEmit({ type: 'run.state', threadId, runId, state: 'PLANNING' });
      steps.forEach((step, i) => setTimeout(
        () => liveEmit({ type: 'tool', threadId, runId, ...step }), 600 + i * 1000));
      setTimeout(() => liveEmit({ type: 'run.end', threadId, runId, state: 'COMPLETED', summary: '' }),
        duration - 150);
    }
    setTimeout(() => {
      list.push({
        role: 'assistant', author: agent?.name || 'agent', at: iso(),
        text: script?.text
          || '(Demo) Nothing runs in demo mode — this is where the reply would appear.',
        ...(script?.cards?.length ? { cards: script.cards } : {}),
      });
      if (thread) thread.lastActivity = iso();
    }, duration);
    return { runId };
  },
  // Agent-to-agent traffic bound to a room. Read-only in the console, and
  // the reason the room keeps it in its own feed rather than the chat.
  // Owner preferences, defaulted on read exactly as the control plane does,
  // so the sheet can be reviewed without a deploy.
  settings: async () => {
    await wait(80);
    return {
      notifications: { completion: true, inputNeeded: true, failure: true, ...SETTINGS.notifications },
      theme: SETTINGS.theme ?? demoTheme() ?? 'dark',
      pinned: SETTINGS.pinned ?? (MODE === 'full' ? ['dm-cos', 'dm-eng'] : []),
      defaultTimezone: SETTINGS.defaultTimezone ?? null,
      workspaceName: SETTINGS.workspaceName ?? null,
      // A working org has been set up, so plain ?demo=1 opens on the inbox.
      // `fresh` and `offer` are accounts that never went through setup.
      onboardedAt: SETTINGS.onboardedAt ?? (MODE === 'full' ? iso(-30 * 24 * 60 * 60_000) : null),
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
    if ('theme' in changes) { SETTINGS.theme = changes.theme; try { localStorage.setItem('amazai.demo.theme', changes.theme); } catch { /* fine */ } }
    if ('pinned' in changes) {
      if (!Array.isArray(changes.pinned)) throw new Error('pinned must be a list of conversation ids');
      const pins = [...new Set(changes.pinned)];
      if (pins.length > 12) throw new Error('at most 12 conversations can be pinned');
      SETTINGS.pinned = pins;
    }
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

  run: async (id) => {
    await wait(120);
    const run = RUNS[id];
    return { state: !run || Date.now() >= run.doneAt ? 'COMPLETED' : 'EXECUTING', approvals: [] };
  },
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
  liveEmit = onEvent;

  at(300, () => onStatus?.('connected'));

  const script = [
    [1400, { type: 'tool', threadId: 't-deploy', name: 'shell', summary: 'npm run build  →  built in 4.21s',
             review: { decision: 'allowed', rule: 'sandbox', reason: 'runs in this Bot\u2019s own sandbox', matched: 'shell' } }],
    [2200, { type: 'tool', threadId: 't-deploy', name: 'file_operations', summary: 'sync dist/ → s3://amazai-console-example (14 objects)',
             review: { decision: 'allowed', rule: 'sandbox', reason: 'runs in this Bot\u2019s own sandbox', matched: 'file_operations' } }],
    [2700, { type: 'handoff', threadId: 't-deploy', handoff: {
      handoffId: 'hoff_31ab', fromAgentId: 'eng', toAgentId: 'ops', status: 'proposed',
      goal: 'Confirm the distribution is serving the new bundle once the invalidation clears.',
      constraints: ['Read-only: no stack changes', 'Stop and report if the 5xx rate moves'],
      grantsOffered: [],
    } }],
    [3000, { type: 'run.state', threadId: 't-deploy', runId: 'run-9a22', costUsd: 0.128 }],

    // The rest of the cast, so every state the inbox draws can be seen in one
    // sitting: Research is waiting on something outside itself, Cloud
    // Operations picks up Engineering's handoff, works, and finishes.
    [1200, { type: 'run.state', threadId: 'dm-res', runId: 'run-res', state: 'AWAITING_CONNECTOR' }],
    [3400, { type: 'tool', threadId: 'dm-ops', runId: 'run-ops', name: 'cloudwatch',
             summary: 'Reading the 5xx rate, last 6 hours' }],
    [9000, { type: 'run.end', threadId: 'dm-ops', runId: 'run-ops', state: 'COMPLETED',
             summary: 'No 5xx since the rollback.' }],
  ];
  script.forEach(([ms, ev]) => at(ms, () => onEvent(ev)));

  // Streamed into `dm-eng`, the thread the conversation screen actually opens.
  // These words used to go to `t-deploy`, which no screen in the current
  // console renders, so the one part of the demo that shows a reply arriving
  // could not be seen. The approval that follows stays on the same thread, so
  // the sequence a reviewer is meant to judge -- words, then the thing that
  // needs them -- happens in one place.
  const prose = 'Build is clean and the bundle is on S3. The distribution is still serving the '
    + 'previous index.html from cache, so the last step is an invalidation — which is immediate '
    + 'and irreversible, so it needs you.';
  const words = prose.split(/(?<= )/);
  at(3400, () => onEvent({ type: 'run.state', threadId: 'dm-eng', runId: 'run-eng', state: 'EXECUTING' }));
  words.forEach((word, i) => {
    at(3600 + i * 34, () => onEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-eng', text: word }));
  });

  at(3600 + words.length * 34 + 400, () => {
    onEvent({ type: 'approval.requested', threadId: 'dm-eng', runId: 'run-eng', approval: APPROVAL });
  });

  return {
    send: () => {},
    close: () => { dead = true; liveEmit = null; timers.forEach(clearTimeout); },
  };
}


/* ------------------------------------------------------------ the runtime */

/*
 * Everything below reproduces what the control plane *does*, not what it looks
 * like: a run has a state, pauses on an approval that names its rule, resumes
 * when decided, can be stopped or redirected, and writes the same history lines
 * the API writes. Every shape here was first checked against the real handler in
 * `tests/test_runtime_loop.py` and `tests/test_composer_api.py`.
 *
 * Guarded so none of it (and none of the fixtures it references) survives a
 * production build -- an unguarded top-level statement here is a side effect the
 * bundler cannot prove away.
 */
if (import.meta.env.DEV) {
  const PENDING = [APPROVAL];
  const RUN = {};                       // runId -> { agentId, threadId, state, timers, approvals, redirectedTo }
  const SHARED = [{ memId: 'mem_name', title: 'Name', body: 'Jaylen', kind: 'foundational',
                    scope: 'shared_user', source: 'user', status: 'published', pinned: true }];
  const SKILLS = [
    { skillId: 'weekly-plan', name: 'Weekly plan', description: 'Plan the week around deep work and batched meetings.',
      status: 'active', currentVersion: 1 },
    { skillId: 'incident-report', name: 'Incident report', description: 'Turn an alarm timeline into a written report.',
      status: 'proposed', currentVersion: 1, proposedBy: 'ops' },
  ];
  const seen = new Set();
  const uid = () => Math.random().toString(36).slice(2, 8);
  const lower = (t) => t.replace(/^./, (c) => c.toLowerCase());
  const event = (threadId, text, icon = 'check', extra = {}) =>
    (MESSAGES[threadId] ||= []).push({ role: 'system', kind: 'event', author: 'system', text, icon, ...extra });
  const nameOf = (id) => AGENTS.find((a) => a.agentId === id)?.name || id;
  const emit = (ev) => liveEmit?.(ev);

  const REVIEW = {
    read: (m) => ({ decision: 'allowed', rule: 'read', reason: 'read-only', matched: m || 'read' }),
    sandbox: (m) => ({ decision: 'allowed', rule: 'sandbox', reason: 'runs in this Bot’s own sandbox', matched: m }),
    memory: { decision: 'allowed', rule: 'memory', reason: 'writes only to this Bot’s own memory' },
    proposal: { decision: 'allowed', rule: 'proposal', reason: 'a suggestion; you review it before it exists' },
    floor: (m) => ({ decision: 'asked', rule: 'floor', reason: 'on the always-approve floor', matched: m }),
    approved: (id) => ({ decision: 'allowed', rule: 'approved', reason: 'you approved these exact arguments', matched: id }),
  };
  const verdictFor = (step) => step.review
    || (step.name === 'memory' ? REVIEW.memory : step.name === 'connectors' ? REVIEW.read('connectors') : REVIEW.sandbox(step.name));

  const card = (a) => ({ ...a, requestedAt: iso(-1000), expiresAt: a.expiresAt || iso(15 * 60_000) });

  function persist(run, { text = '', steps = [], cards, startedAt }) {
    if (!text && !steps.length && !cards?.length) return;
    (MESSAGES[run.threadId] ||= []).push({
      role: 'assistant', author: nameOf(run.agentId), text, at: iso(),
      ...(steps.length ? { steps, startedAt, endedAt: iso() } : {}),
      ...(cards?.length ? { cards } : {}),
    });
    const t = THREADS.find((x) => x.threadId === run.threadId);
    if (t && text) t.lastActivity = iso();
  }

  function finish(runId, state, summary = '') {
    const run = RUN[runId];
    if (!run) return;
    run.state = state;
    run.timers.forEach(clearTimeout);
    emit({ type: 'run.end', threadId: run.threadId, runId, state, summary });
  }

  /**
   * Play a plan through a run: each step is a pushed `tool` event carrying its
   * verdict, then either a pause on an approval, or the reply.
   */
  function play(runId, plan) {
    const run = RUN[runId];
    const startedAt = run.startedAt = iso();
    const done = run.done = [];
    const at = (ms, fn) => run.timers.push(setTimeout(() => { if (run.state !== 'CANCELLED') fn(); }, ms));
    emit({ type: 'run.state', threadId: run.threadId, runId, state: 'PLANNING' });

    plan.steps.forEach((step, i) => at(600 + i * 900, () => {
      const review = verdictFor(step);
      done.push({ name: step.name, summary: step.summary, review, at: iso() });
      emit({ type: 'tool', threadId: run.threadId, runId, name: step.name, summary: step.summary, review });
    }));
    const end = 600 + plan.steps.length * 900;

    if (plan.pause) {
      at(end, () => {
        const approval = { ...plan.pause, approvalId: `apv-${uid()}`, runId, status: 'pending',
                           requestedBy: { agentId: run.agentId } };
        done.push({ name: plan.pause.action, summary: plan.pauseSummary || 'waiting for your approval',
                    review: plan.pause.policy && { decision: 'asked', ...plan.pause.policy }, at: iso() });
        PENDING.push(card(approval));
        run.approvals = [approval.approvalId];
        run.state = 'AWAITING_APPROVAL';
        run.resume = { plan, done, startedAt, approval };
        persist(run, { text: plan.reply?.text || '', steps: done, cards: plan.reply?.cards, startedAt });
        emit({ type: 'approval.requested', threadId: run.threadId, runId, approval: card(approval) });
      });
    } else {
      at(end, () => {
        persist(run, { text: plan.reply.text, steps: done, cards: plan.reply.cards, startedAt });
        run.state = 'COMPLETED';
        emit({ type: 'run.end', threadId: run.threadId, runId, state: 'COMPLETED', summary: '' });
      });
    }
  }

  function begin(threadId, agentId, plan) {
    const runId = `run-demo-${uid()}`;
    RUN[runId] = { agentId, threadId, state: 'EXECUTING', timers: [], approvals: [], redirectedTo: null };
    play(runId, plan);
    return runId;
  }

  function planFor(threadId, agentId, text) {
    const first = FIRST_STEPS[text];
    if (first) {
      return { steps: first.steps, reply: { text: first.text, cards: first.cards },
        // Deliver first, then offer -- as the real loop does: the offer is a
        // proposal made *after* the result, and a Bot proposal is an approval.
        ...(text === 'Planning my day and week' ? {
          pause: { action: 'agent.create', risk: 'high', capability: 'admin', reversible: false,
                   arguments: { name: 'Calendar', role: 'Keeps your calendar, prep and scheduling in order.',
                                avatar: { shape: 'cloud', color: '#12a594' } },
                   why: 'Scheduling is a separate lane from planning.',
                   target: { parentAgentId: agentId },
                   policy: { rule: 'floor', matched: 'agent.create', reason: 'on the always-approve floor' } },
          pauseSummary: 'proposed Calendar',
        } : {}) };
    }
    if (/\b(slack|announce|post)\b/i.test(text)) {
      return { steps: [{ name: 'slack.read', summary: 'Reading #launch, last 20 messages', review: REVIEW.read('slack.read') }],
        pause: { action: 'slack.post', risk: 'medium', capability: 'write', reversible: false,
                 arguments: { channel: '#launch', text: 'The console is live. Thanks all.' },
                 why: 'slack.post needs your approval (on the always-approve floor)',
                 policy: { rule: 'floor', matched: 'slack.post', reason: 'on the always-approve floor' } },
        pauseSummary: 'waiting for your approval' };
    }
    const skill = /^\/([\w-]+)/.exec(text);
    if (skill && SKILLS.some((k) => k.skillId === skill[1] && k.status === 'active')) {
      const name = SKILLS.find((k) => k.skillId === skill[1]).name;
      return { steps: [{ name: 'memory', summary: `Applying the ${name} skill` }],
               reply: { text: `Using your ${name} skill: mornings for deep work, meetings after lunch, Friday open.` } };
    }
    return { steps: [], reply: { text: '(Demo) Nothing runs in demo mode — this is where the reply would appear.' } };
  }

  /** Stop a run and, if asked, chain what comes next -- what the API and the orchestrator do together. */
  function stopRun(runId, redirectText) {
    const run = RUN[runId];
    if (!run || ['COMPLETED', 'CANCELLED'].includes(run.state)) return null;
    run.timers.forEach(clearTimeout);
    (run.approvals || []).forEach((id) => { const a = PENDING.find((p) => p.approvalId === id); if (a) a.status = 'denied'; });
    run.state = 'CANCELLED';
    // What was already done is kept: a run stopped halfway still happened, and
    // its trail is still evidence (`_settle_cancelled` in the orchestrator).
    if (run.done?.length) persist(run, { steps: run.done, startedAt: run.startedAt });
    emit({ type: 'run.end', threadId: run.threadId, runId, state: 'CANCELLED', summary: 'stopped by you' });
    if (redirectText) {
      event(run.threadId, `Redirected: ${nameOf(run.agentId)} stopped and picked up your new message`);
      const next = begin(run.threadId, run.agentId, planFor(run.threadId, run.agentId, redirectText));
      run.redirectedTo = next;
      return next;
    }
    return null;
  }

  Object.assign(demoApi, {
    send: async (threadId, text, opts = {}) => {
      await wait(150);
      const thread = THREADS.find((t) => t.threadId === threadId);
      const list = (MESSAGES[threadId] ||= []);
      list.push({ role: 'user', author: 'you', text, at: iso() });
      if (thread) thread.lastActivity = iso();

      // A room starts every member on the task in parallel. An @ mention is an
      // intentional way to narrow a follow-up to particular Bots.
      if (thread?.kind === 'room') {
        const named = thread.agentIds.filter((id) => new RegExp(`(?<![\\w-])@${id}(?![\\w-])`).test(text));
        const targets = named.length ? named : thread.agentIds;
        if (targets.length > 1) {
          event(threadId, `Woke ${targets.length === 2 ? targets.map(nameOf).join(' and ') : targets.map(nameOf).join(', ')}`);
        }
        const runs = targets.map((agentId) => ({
          agentId, runId: begin(threadId, agentId, agentId === 'ops'
            ? { steps: [{ name: 'cloudwatch', summary: 'Reading the 5xx rate, last 6 hours', review: REVIEW.read('cloudwatch') }],
                reply: { text: 'No 5xx since the rollback; alarms are quiet.' } }
            : { steps: [{ name: 'shell', summary: 'git status  →  clean', review: REVIEW.sandbox('shell') }],
                reply: { text: `${nameOf(agentId)} here — nothing blocking on my side.` } }),
        }));
        return { runId: runs[0].runId, state: 'EXECUTING', runs };
      }

      const agentId = thread?.agentIds?.[0];
      const active = opts.redirectRunId && RUN[opts.redirectRunId];
      if (active && !['COMPLETED', 'CANCELLED'].includes(active.state)) {
        stopRun(opts.redirectRunId, text);
        return { runId: opts.redirectRunId, state: 'CANCELLING',
                 runs: [{ runId: opts.redirectRunId, agentId, state: 'CANCELLING', redirected: true }] };
      }
      const runId = begin(threadId, agentId, planFor(threadId, agentId, text));
      return { runId, state: 'EXECUTING', runs: [{ runId, agentId, state: 'EXECUTING' }] };
    },

    run: async (id) => {
      await wait(100);
      const r = RUN[id];
      if (!r) return { state: 'COMPLETED', approvals: [] };
      return { state: r.state, redirectedTo: r.redirectedTo,
               approvals: PENDING.filter((a) => a.runId === id).map(card) };
    },

    // A stop that works in every state, including paused -- as the API's does.
    cancel: async (id) => { await wait(120); stopRun(id); return { runId: id, state: 'CANCELLING' }; },

    approvals: async (status = 'pending') => (await wait(80), {
      approvals: PENDING.filter((a) => (status ? a.status === status : true)).map(card),
    }),

    decide: async (runId, approvalId, approve) => {
      await wait(200);
      const approval = PENDING.find((a) => a.approvalId === approvalId);
      if (!approval) return {};
      approval.status = approve ? 'approved' : 'denied';
      const run = RUN[runId];
      if (!run?.resume) return { approval: card(approval), resumed: false };
      const { plan, done, startedAt } = run.resume;
      run.state = 'EXECUTING';
      emit({ type: 'run.state', threadId: run.threadId, runId, state: 'EXECUTING' });

      if (approval.action === 'agent.create') {
        if (approve) {
          const created = await demoApi.createAgent({ name: approval.arguments.name, role: approval.arguments.role,
                                                       avatar: approval.arguments.avatar, title: 'Calendar' });
          event(run.threadId, `Created ${created.name}`);
        }
        finish(runId, 'COMPLETED');
      } else if (approval.action === 'slack.post') {
        setTimeout(() => {
          if (approve) {
            const step = { name: 'slack.post', summary: `posted to ${approval.arguments.channel}`,
                           review: REVIEW.approved(approvalId), at: iso() };
            emit({ type: 'tool', threadId: run.threadId, runId, name: step.name, summary: step.summary, review: step.review });
            persist(run, { text: `Posted to ${approval.arguments.channel}.`, steps: [step], startedAt: iso() });
          } else {
            persist(run, { text: 'Understood — I haven’t posted anything.' });
          }
          finish(runId, 'COMPLETED');
        }, 700);
      } else {
        finish(runId, 'COMPLETED');
      }
      run.resume = null;
      return { approval: card(approval), resumed: true };
    },

    // --- memory: written by real actions, corrected in place ----------------
    addMemory: async (id, entry) => {
      await wait(120);
      const agent = AGENTS.find((a) => a.agentId === id);
      const row = { memId: `mem_${uid()}`, title: entry.title || '', body: entry.body, scope: 'agent',
                    kind: entry.kind || (entry.pinned === false ? 'note' : 'foundational'),
                    source: 'user', status: 'published', pinned: entry.pinned !== false, usedCount: 0 };
      (agent.memory ||= []).push(row);
      event(`dm-${id}`, `Saved to memory: ${(row.title || row.body).slice(0, 80)}`, 'layers', { memId: row.memId });
      return row;
    },
    updateMemory: async (id, memId, changes) => {
      await wait(120);
      const row = AGENTS.find((a) => a.agentId === id)?.memory?.find((m) => m.memId === memId);
      if (!row) throw new Error('No such memory');
      Object.assign(row, changes, { pinned: (changes.kind || row.kind) === 'foundational',
                                    correctedBy: 'you', correctedAt: iso() });
      event(`dm-${id}`, `Memory corrected: ${(row.title || row.body).slice(0, 80)}`, 'layers');
      return { ...row };
    },
    deleteMemory: async (id, memId) => {
      const agent = AGENTS.find((a) => a.agentId === id);
      if (agent) agent.memory = (agent.memory || []).filter((m) => m.memId !== memId);
      return null;
    },
    sharedMemory: async () => (await wait(80), { memory: SHARED.map((m) => ({ ...m })) }),
    addSharedMemory: async (entry) => {
      const row = { memId: `mem_${uid()}`, title: entry.title, body: entry.body, kind: 'foundational',
                    scope: 'shared_user', source: 'user', status: 'published', pinned: true };
      SHARED.push(row);
      return row;
    },
    updateSharedMemory: async (memId, changes) => {
      const row = SHARED.find((m) => m.memId === memId);
      if (!row) throw new Error('No such memory');
      return Object.assign(row, changes, { correctedBy: 'you', correctedAt: iso() });
    },
    deleteSharedMemory: async (memId) => {
      const at = SHARED.findIndex((m) => m.memId === memId);
      if (at >= 0) SHARED.splice(at, 1);
      return null;
    },

    // --- skills ----------------------------------------------------------------
    skills: async () => (await wait(80), { skills: SKILLS.map((k) => ({ ...k })) }),
    createSkill: async (body) => {
      await wait(150);
      const { sourceThreadId, ...fields } = body;
      const skill = { skillId: fields.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
                      status: 'active', currentVersion: 1, ...fields };
      SKILLS.push(skill);
      if (sourceThreadId && MESSAGES[sourceThreadId]) event(sourceThreadId, `Saved as a skill: ${skill.name}`, 'file');
      return skill;
    },
    updateSkill: async (id, changes) => Object.assign(SKILLS.find((k) => k.skillId === id) || {}, changes),
    assignSkill: async (skillId, agentId, version) => {
      const agent = AGENTS.find((a) => a.agentId === agentId);
      if (agent && !(agent.skillAssignments ||= []).some((a) => a.skillId === skillId)) {
        agent.skillAssignments.push({ skillId, version });
      }
      return { skillId, agentId, version };
    },

    // --- routines and channels ----------------------------------------------------
    runRoutine: async (id, key) => {
      await wait(150);
      const routine = ROUTINES.find((r) => r.routineId === id);
      if (!routine || routine.status === 'archived') throw new Error('an archived routine cannot be run');
      const dedupe = `${id}:${key}`;
      const runId = `run-demo-${uid()}`;
      if (seen.has(dedupe)) return { ok: true, deduplicated: true, runId };
      seen.add(dedupe);
      routine.lastRun = iso();
      return { ok: true, runId, threadId: routine.threadId || `th-${id}` };
    },
    patchThread: async (id, changes) => {
      await wait(150);
      const room = THREADS.find((t) => t.threadId === id);
      if (!room || room.kind !== 'room') throw new Error('only a room’s members can be changed');
      if (changes.title) room.title = changes.title;
      if (changes.agentIds) {
        const ids = [...new Set(changes.agentIds)];
        if (!ids.length) throw new Error('a room needs at least one agent');
        if (ids.length > 6) throw new Error('a room holds at most 6 agents');
        const unknown = ids.find((a) => !AGENTS.some((x) => x.agentId === a));
        if (unknown) throw new Error(`no such agent '${unknown}'`);
        ids.filter((a) => !room.agentIds.includes(a)).forEach((a) => event(id, `${nameOf(a)} joined`));
        room.agentIds.filter((a) => !ids.includes(a)).forEach((a) => event(id, `${nameOf(a)} left`));
        room.agentIds = ids;
      }
      return { ...room };
    },
  });

  // Connectors, shaped like the real API (Composio). Nothing here reaches a real
  // service: "connecting" just marks the app installed, so the flow can be reviewed.
  const APPS = [
    ['gmail', 'Gmail', 'Read, search and draft email.'],
    ['googlecalendar', 'Google Calendar', 'See your schedule and find time.'],
    ['slack', 'Slack', 'Read channels and post messages.'],
    ['github', 'GitHub', 'Repositories, issues and pull requests.'],
    ['notion', 'Notion', 'Pages and databases.'],
    ['linear', 'Linear', 'Issues and projects.'],
    ['googledrive', 'Google Drive', 'Find and read files.'],
    ['hubspot', 'HubSpot', 'Contacts and deals.'],
  ].map(([slug, name, description]) => ({ slug, name, description, logo: '', categories: [], toolsCount: null, noAuth: false }));
  const installedApps = new Map();
  Object.assign(demoApi, {
    connectorApps: async (q) => {
      const term = (q || '').toLowerCase();
      return { apps: APPS.filter((a) => !term || a.name.toLowerCase().includes(term) || a.slug.includes(term)),
               pageInfo: { end_cursor: '' } };
    },
    connectors: async () => ({ connectors: [...installedApps.values()] }),
    connectorAccounts: async () => ({ accounts: [] }),
    connectToken: async () => ({ connectLinkUrl: 'about:blank', accountId: 'ca_demo' }),
    installConnector: async (connectorId) => {
      const slug = connectorId.replace(/^composio:/, '');
      const app = APPS.find((a) => a.slug === slug);
      if (!app) throw new Error('That app is not available.');
      const row = { connectorId: `composio:${slug}`, app: slug, name: app.name, status: 'installed',
                    capability: 'admin', allowedTools: ['*'], accountId: 'ca_demo' };
      installedApps.set(row.connectorId, row);
      // Mirrors the API: connecting grants it to every active Bot that does not already hold it.
      const grantedTo = [];
      for (const a of AGENTS) {
        if ((a.status || 'active') !== 'active' || (a.grants || []).some((g) => g.connectorId === row.connectorId)) continue;
        a.grants = [...(a.grants || []), { connectorId: row.connectorId, capability: 'admin', allowedTools: ['*'] }];
        grantedTo.push(a.agentId);
      }
      return { ...row, grantedTo };
    },
    revokeConnector: async (connectorId) => {
      installedApps.delete(connectorId);
      for (const a of AGENTS) a.grants = (a.grants || []).filter((g) => g.connectorId !== connectorId);
      return { connectorId, revokedFrom: [] };
    },
  });

  // A routine created in the console says so in that Bot's conversation.
  const createRoutine = demoApi.createRoutine;
  demoApi.createRoutine = async (body) => {
    const routine = await createRoutine(body);
    event(`dm-${routine.agentId}`, `Routine created: ${routine.name} · ${lower(describeSchedule(routine.trigger))}`, 'clock',
          { routineId: routine.routineId });
    return routine;
  };
}
