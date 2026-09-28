import { useSyncExternalStore } from 'react';

import { stepLabel } from './lib/tools';

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

/**
 * Seed the live store from a durable-state snapshot (GET /presence).
 *
 * The socket only tells you what changes *while you are listening*, so on
 * reload this store is empty until the next event -- a Bot that has been
 * working the whole time looks idle until it happens to do something. The
 * backend derives the same answer from the RUN#/TASK# rows that already exist
 * (`services/amazai/presence.py`), and this folds that snapshot in through the
 * exact same `put()` the socket uses, so a seeded Companion is indistinguishable
 * from a live-driven one. The socket then keeps it current.
 *
 * The backend descriptor names two states this store spells differently:
 * `needs_approval` is this store's `approval`, and `done` is its `complete`
 * (which fades on the same timer a live `run.end` does). `idle` is absence, so
 * it is skipped -- `put()` would only remove an entry that a later event might
 * have added.
 *
 * The seed does NOT clobber a live entry. `PresenceFeed` opens the socket
 * synchronously and folds this snapshot in from a later-resolving `/presence`
 * fetch, so a live `run.state`/`run.end` event can land first; the snapshot it
 * races is a moment older, so overwriting with it would replace fresher state
 * with staler (and re-arm the 4s `complete` fade the live `run.end` already
 * started). So an agent the socket has already touched is skipped here -- the
 * live event wins, and the snapshot only fills in the agents no event has
 * reached yet. The socket's own `applyEvent` path is unchanged.
 */
const SEED_STATES = { needs_approval: 'approval', done: 'complete' };

export function seed(data) {
  for (const p of data?.presence || []) {
    if (!p?.agentId || p.state === 'idle') continue;
    if (p.agentId in snapshot) continue;  // a live event already set this one; do not overwrite it
    put(p.agentId, SEED_STATES[p.state] || p.state, p.action || '', p.runId || '');
  }
}

/** For tests and for switching accounts. */
export function resetPresence() {
  timers.forEach(clearTimeout);
  timers.clear();
  commit({});
  stepsSnapshot = {};
  stepListeners.forEach((fn) => fn());
  streamSnapshot = {};
  streamListeners.forEach((fn) => fn());
}

/* ------------------------------------------------------ the socket itself */

// Whether the console is hearing anything. `ws.js` already worked this out and
// computed the wording; nothing read it, so a socket that had silently died
// looked exactly like a Bot with nothing to say -- frozen animations, a
// conversation that never updates, and no way to tell which.
let connection = { status: 'connecting' };
const connListeners = new Set();

export function setConnection(status) {
  if (connection.status === status) return;
  connection = { status };
  connListeners.forEach((fn) => fn());
}

/** `{ status }`: 'connecting' | 'connected' | 'disconnected' | 'reconnecting in Ns' | 'unconfigured'. */
export function useConnection() {
  return useSyncExternalStore(
    (fn) => { connListeners.add(fn); return () => connListeners.delete(fn); },
    () => connection,
  );
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

/* --------------------------------------------------------- the words, live */

// The reply as it is being written, per thread. Live only, for the same reason
// the steps are: the finished words are on the stored message, and this is that
// same text before it lands. `delta` text used to be thrown away here -- the
// event was reduced to "Writing a reply" and the words discarded -- so the
// console showed an animated dot over a 1.5s poll instead of the reply.
let streamSnapshot = {};    // threadId -> { runId, text, at }
const streamListeners = new Set();

function commitStream(threadId, entry) {
  if (entry) {
    streamSnapshot = { ...streamSnapshot, [threadId]: entry };
  } else {
    if (!(threadId in streamSnapshot)) return;
    const { [threadId]: _gone, ...rest } = streamSnapshot;
    streamSnapshot = rest;
  }
  streamListeners.forEach((fn) => fn());
}

function appendDelta(ev) {
  if (!ev.threadId || !ev.text) return;
  const cur = streamSnapshot[ev.threadId];
  // A different run means a different reply: start over rather than appending
  // this turn's words to the last one's.
  const sameRun = cur && (!ev.runId || !cur.runId || cur.runId === ev.runId);
  commitStream(ev.threadId, sameRun
    ? { ...cur, text: cur.text + ev.text, at: Date.now() }
    : { runId: ev.runId || null, text: ev.text, at: Date.now() });
}

/**
 * Drop a thread's live text, once its stored copy is on screen.
 *
 * Deliberately the consumer's call and not something `run.end` does. The server
 * writes the assistant message and *then* the run ends, so clearing on the
 * event would blank the reply for as long as the reload takes and then bring it
 * back. Clearing after the stored message is in hand has no such gap -- and if
 * that reload fails, the streamed words stay on screen, which is the better of
 * the two ways to be wrong.
 */
export function clearStream(threadId) {
  commitStream(threadId, null);
}

/** The reply being written in this thread, or undefined. */
export function useStreamingText(threadId) {
  return useSyncExternalStore(
    (fn) => { streamListeners.add(fn); return () => streamListeners.delete(fn); },
    () => streamSnapshot[threadId],
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
// `EXECUTING` is `thinking`, not `working`: this fires once, before the
// stream has produced a single delta or tool call -- the very first thing a
// Bot does is reason, not act, and `working` here drew that instant wrong
// until either a `delta` or a `tool` event corrected it a moment later.
const RUN_STATES = {
  QUEUED:             ['thinking', 'Getting started'],
  PLANNING:           ['thinking', 'Planning'],
  EXECUTING:          ['thinking', ''],
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
      // Kept, not just counted. A room is the exception: `delta` carries a
      // runId but no agentId, so with several Bots writing at once there is no
      // way to say whose words these are, and interleaving them unattributed
      // would be worse than not showing them. A room therefore accumulates
      // nothing here -- see the note in Room.jsx.
      if (agents.length === 1) appendDelta(ev);
      agents.forEach((a) => put(a, 'thinking', 'Writing a reply', ev.runId));
      break;
    case 'tool':
      recordStep(ev);
      // The summary is already a sentence. Falling back to the bare tool name
      // put an identifier (`agent.find`) where a person reads what a Bot is
      // doing right now, so the fallback is the labelled name.
      agents.forEach((a) => put(a, 'working',
                                ev.summary || stepLabel(ev.name) || '', ev.runId));
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
