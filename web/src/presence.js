import { useSyncExternalStore } from 'react';

/**
 * Who is doing what, right now.
 *
 * The status memo separates four questions that a single badge would blur:
 * *who* (the companion's shape), *doing what* (its motion), *how much do I
 * need to know* (a line of text on hover), and *do I need to look*
 * (attention: unread, needs you). This store answers the second and third. It
 * holds a lifecycle state and one line of action per companion, and nothing
 * else -- unread and needs-you are server facts the inbox already reads, and
 * keeping them out of here is what stops a companion that is merely busy from
 * ever looking like one that needs you.
 *
 * It is derived from the events the control plane already pushes
 * (`services/amazai/push.py`), not from anything new, and it is a cache: a
 * missed event means a stale animation until the next one, never a wrong
 * answer about what an agent is *allowed* to do.
 */

const COMPLETE_MS = 4_000;
// A failure stays visible long enough to be noticed on return, but not
// forever: an agent that failed an hour ago and has been fine since should not
// still be drawn as stuck.
const BLOCKED_MS = 120_000;

let snapshot = {};                 // agentId -> { state, action, at }
const listeners = new Set();
const timers = new Map();          // agentId -> timeout, for states that fade

function commit(next) {
  snapshot = next;
  listeners.forEach((fn) => fn());
}

/** Set an agent's state. `idle` removes the entry: absence *is* idle. */
export function put(agentId, state, action = '', runId = '') {
  if (!agentId) return;
  clearTimeout(timers.get(agentId));
  timers.delete(agentId);

  if (state === 'idle') {
    if (!(agentId in snapshot)) return;
    const { [agentId]: _gone, ...rest } = snapshot;
    commit(rest);
    return;
  }

  // `runId` is what lets a screen opened mid-run offer "Stop now" for a run it
  // did not start: the socket said which run this state belongs to.
  commit({ ...snapshot, [agentId]: { state, action, runId, at: Date.now() } });
  const ttl = state === 'complete' ? COMPLETE_MS : state === 'blocked' ? BLOCKED_MS : 0;
  if (ttl) timers.set(agentId, setTimeout(() => put(agentId, 'idle'), ttl));
}

/** For tests and for switching accounts. */
export function resetPresence() {
  timers.forEach(clearTimeout);
  timers.clear();
  commit({});
  stepsSnapshot = {};
  stepListeners.forEach((fn) => fn());
}

/* ---------------------------------------------------------------- the steps */

// The most recent run's tool calls, per thread. Live only: it is fed by the
// socket and gone on reload, because what happened is already in the run's
// sealed evidence and this is not a second copy of it -- it is the trail as it
// is being laid, for someone who is watching.
let stepsSnapshot = {};    // threadId -> { runId, startedAt, endedAt, items }
const stepListeners = new Set();

function commitSteps(threadId, entry) {
  stepsSnapshot = { ...stepsSnapshot, [threadId]: entry };
  stepListeners.forEach((fn) => fn());
}

function recordStep(ev) {
  if (!ev.threadId) return;
  const cur = stepsSnapshot[ev.threadId];
  // `review` is Auto Review's verdict, decided by the control plane. It is
  // carried through untouched: this store describes, it never re-decides.
  const step = { name: ev.name || 'tool', summary: ev.summary || '',
                 review: ev.review || null, at: Date.now() };
  const newRun = !cur || cur.endedAt || (ev.runId && cur.runId && cur.runId !== ev.runId);
  commitSteps(ev.threadId, newRun
    ? { runId: ev.runId || null, startedAt: step.at, endedAt: null, items: [step] }
    : { ...cur, items: [...cur.items, step] });
}

function endSteps(ev) {
  const cur = stepsSnapshot[ev.threadId];
  if (cur && !cur.endedAt) commitSteps(ev.threadId, { ...cur, endedAt: Date.now() });
}

/** The latest run's steps for a thread, or undefined if none has been seen. */
export function useSteps(threadId) {
  return useSyncExternalStore(
    (fn) => { stepListeners.add(fn); return () => stepListeners.delete(fn); },
    () => stepsSnapshot[threadId],
  );
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** `{ [agentId]: { state, action } }` for everyone who is not idle. */
export function usePresence() {
  return useSyncExternalStore(subscribe, () => snapshot);
}

/* ------------------------------------------------------------- the events */

// Run states -> [companion state, a line for hover]. `AWAITING_INPUT` and
// `AWAITING_LOGIN` are asking *you*, so they are `approval` ("Needs you");
// `AWAITING_CONNECTOR` is waiting on the outside world, which is `waiting`.
const RUN_STATES = {
  QUEUED:             ['thinking', 'Getting started'],
  PLANNING:           ['thinking', 'Planning'],
  EXECUTING:          ['working', ''],
  RETRYING:           ['working', 'Retrying'],
  AWAITING_APPROVAL:  ['approval', 'Waiting for your approval'],
  AWAITING_INPUT:     ['approval', 'Waiting for your answer'],
  AWAITING_LOGIN:     ['approval', 'Waiting for you to sign in'],
  AWAITING_CONNECTOR: ['waiting', 'Waiting on a connector'],
  SUSPENDED:          ['waiting', 'Paused'],
  CANCELLING:         ['waiting', 'Stopping'],
};

const TERMINAL = new Set(['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'EXPIRED']);

function ended(agents, state, summary) {
  const s = String(state || '').toUpperCase();
  if (s === 'COMPLETED' || s === 'PARTIAL') agents.forEach((a) => put(a, 'complete', summary || ''));
  else if (s === 'FAILED' || s === 'EXPIRED') agents.forEach((a) => put(a, 'blocked', summary || 'The last run did not finish'));
  else agents.forEach((a) => put(a, 'idle'));   // cancelled: the operator's own doing
}

/**
 * Fold one pushed event into the store.
 *
 * `ctx.agentsForThread(threadId)` maps a thread to the agents a run there
 * belongs to. A direct thread is `dm-<agentId>`; a task thread has one agent;
 * a room has several and no way to say which is running, so a room's deltas
 * and tool calls are left alone rather than lit up on everyone in it.
 */
export function applyEvent(ev, ctx) {
  const agents = ctx.agentsForThread(ev.threadId);

  switch (ev.type) {
    case 'run.state': {
      const s = String(ev.state || '').toUpperCase();
      if (RUN_STATES[s]) agents.forEach((a) => put(a, ...RUN_STATES[s], ev.runId));
      else {
        // Only a real terminal state closes the trail; an event carrying no
        // state at all must not end a run that is still going.
        if (TERMINAL.has(s)) endSteps(ev);
        ended(agents, s, '');
      }
      break;
    }
    case 'delta':
      agents.forEach((a) => put(a, 'thinking', 'Writing a reply', ev.runId));
      break;
    case 'tool':
      recordStep(ev);
      agents.forEach((a) => put(a, 'working', ev.summary || ev.name || '', ev.runId));
      break;
    case 'approval.requested': {
      // A pause closes the trail: the turn that stopped here is saved with its
      // steps, and a resumed run starts a fresh one. Leaving this one "working"
      // would draw the same steps twice -- once saved, once live.
      endSteps(ev);
      const who = ev.approval?.requestedBy?.agentId;
      const targets = who ? [who] : agents;
      const what = ev.approval?.action ? `: ${ev.approval.action}` : '';
      targets.forEach((a) => put(a, 'approval', `Needs your approval${what}`, ev.runId));
      break;
    }
    case 'handoff': {
      // Both ends move: the sender is waiting on the recipient, and the
      // recipient has been handed something. The console draws the handoff
      // itself; this is only the motion.
      const { fromAgentId: from, toAgentId: to } = ev.handoff || {};
      if (from) put(from, 'waiting', `Waiting on ${ctx.nameOf(to)}`);
      if (to) put(to, 'thinking', `Picking up a handoff from ${ctx.nameOf(from)}`);
      break;
    }
    case 'run.end':
      endSteps(ev);
      ended(agents, ev.state, ev.summary);
      break;
    default:
      break;
  }
}
