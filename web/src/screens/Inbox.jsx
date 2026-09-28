import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import CreateMenu from '../components/CreateMenu';
import FirstBotOffer from '../components/FirstBotOffer';
import GroupMark from '../components/GroupMark';
import Icon from '../components/Icon';
import Problem from '../components/Problem';
import ProfileSheet from '../components/ProfileSheet';
import { RosterSkeleton } from '../components/Skeleton';
import UserAvatar from '../components/UserAvatar';
import { COPY } from '../lib/errors';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';
import { OFFER, useFirstRun } from '../hooks/useFirstRun';
import { useFlipList } from '../hooks/useFlipList';
import { usePins } from '../hooks/usePins';
import { usePresence } from '../presence';
import { onThreadsChanged } from '../threadsBus';

/**
 * One inbox for every conversation.
 *
 * The console used to answer "what needs me?" on Home and "who do I have?"
 * in Agents, and a room was a third place again. On a phone that is three
 * taps to reach the thing you opened the app for. Here a companion and a
 * room are the same kind of row -- a conversation -- ordered by whichever
 * spoke last, so the answer to all three questions is the first screen.
 *
 * The companion's own state carries the urgency. An agent waiting on an
 * approval is drawn in `approval`, the same state the character system
 * already animates everywhere else, so "needs you" is a property of the
 * companion rather than a separate list that can disagree with it.
 */

/** A DM thread is derived from the agent, so a companion with no conversation
 *  yet still has a row rather than disappearing until it first speaks. */
const dmThreadId = (agentId) => `dm-${agentId}`;

function timeLabel(value) {
  if (!value) return '';
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return '';
  const now = new Date();
  if (at.toDateString() === now.toDateString()) {
    return at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
  return at.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

/** "You: " only when the last word was yours. A Bot-created first task
 *  names the teammate who assigned it instead of impersonating the operator. */
export function previewOf(thread) {
  if (!thread?.preview) return '';
  if (thread.previewRole === 'user') return `You: ${thread.preview}`;
  if (thread.previewRole === 'briefing' && thread.previewAuthor) {
    return `${thread.previewAuthor}: ${thread.preview}`;
  }
  return thread.preview;
}

// States that mean the Bot is in a turn right now -- what the presence dot and
// the live action line are for. `approval` and `blocked` are attention, not
// activity, and get their own colour instead.
const LIVE = new Set(['thinking', 'working', 'waiting']);

/**
 * The four things this inbox exists to surface, apart from ordinary activity.
 *
 * The inbox is "what needs me" -- so a row is meaningful when it is waiting on
 * a decision, waiting on an answer, has just finished work, or has hard-failed.
 * Everything else (a Bot merely thinking or using a tool) is activity, not an
 * item that needs the operator, and stays quiet. This classifies an
 * already-derived row -- it reads presence state and the action line the
 * backend already produced, it does not add a route, a fetch, or a data flow.
 *
 * `approval` and `needs-human-answer` are the same presence bucket over the
 * socket (`AWAITING_APPROVAL` vs `AWAITING_INPUT`/`AWAITING_LOGIN` both light
 * `approval`), so they are told apart by the action line the backend already
 * wrote: "Waiting for your answer"/"sign in" is an answer, anything else in
 * that bucket is an approval. Returns null for a row that is not an item.
 */
export function attentionOf(row) {
  if (row.state === 'approval') {
    const action = (row.action || '').toLowerCase();
    if (action.includes('answer') || action.includes('sign in')) return 'answer';
    return 'approval';
  }
  if (row.state === 'blocked') return 'failed';
  if (row.state === 'complete') return 'done';
  return null;
}

//: Attention kind -> how urgently it should sort to the top. A decision or an
//: answer the operator owes ranks above a hard failure to notice, which ranks
//: above finished work to acknowledge; ordinary activity has no rank and sorts
//: by recency alone.
const ATTENTION_RANK = { approval: 0, answer: 0, failed: 1, done: 2 };

//: The short, meaningful label an attention row carries in place of a live
//: activity line -- aligned with the canonical vocabulary.
const ATTENTION_LABEL = {
  approval: 'Waiting on you', answer: 'Needs your answer',
  failed: "Didn't finish", done: 'Finished',
};

/** One agent (or a room's members), drawn identically in a row and in the pinned
 *  strip. The hover title is the third presence layer -- a line of text for "how
 *  much do I need to know" -- and the accessible name lives on the companion. */
function Mark({ row, size }) {
  const label = STATES[row.state]?.label || 'Idle';
  const title = row.kind === 'room' ? row.title
    : `${row.title} — ${label}${row.action ? `: ${row.action}` : ''}`;
  return (
    <span className="rs-mark" title={title}>
      {row.kind === 'room'
        ? <GroupMark members={row.members} size={size} />
        : <Companion archetype={row.agent.archetype} color={row.agent.color}
                     state={row.state} size={size} name={row.title} />}
      {LIVE.has(row.state) && <i className="rs-live" aria-hidden="true" />}
    </span>
  );
}

/**
 * The one line under the name.
 *
 * While a Bot is in a turn it says what it is doing -- the hover string, shown
 * because a phone has no hover. Otherwise it is the last thing said, or, for a
 * Bot that has said nothing yet, what it is for.
 */
function subline(row) {
  // A meaningful item says what it needs in the canonical short phrase, not a
  // raw activity line -- "Needs your answer" reads as an item, "Typing" does not.
  if (row.attention) return { text: ATTENTION_LABEL[row.attention], live: true, item: true };
  if (row.action && (LIVE.has(row.state) || row.state === 'approval' || row.state === 'blocked')) {
    return { text: row.action, live: true };
  }
  return { text: row.preview || row.subtitle, live: false };
}

/**
 * `variant="pane"` is the same list, rendered by `Shell` beside an open
 * conversation at desktop width instead of standing alone as the `/` route.
 * One component either way -- a second implementation of this list is a
 * second place for a row to disagree with the API about what is unread.
 */
export default function Inbox({ variant }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { agents, loading, error, reload } = useAgents();
  const firstRun = useFirstRun();
  const presence = usePresence();
  const { pins } = usePins();
  const [creating, setCreating] = useState(false);
  const [profile, setProfile] = useState(false);
  const [threads, setThreads] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [feedError, setFeedError] = useState(null);

  useEffect(() => {
    api.threads().then((r) => { setThreads(r.threads || []); setFeedError(null); })
      .catch((e) => setFeedError(e));
    // Approvals are read once here and re-read by the conversation itself.
    // The inbox only needs to know which companions are waiting, not the
    // detail of what they asked for.
    api.approvals('pending').then((r) => setApprovals(r.approvals || []))
      .catch(() => { /* the row still renders without it; never block the inbox */ });

    // A conversation beside this list was opened or answered. Re-read: the API
    // decides what is unread, and the dot on a thread you are looking at has
    // to go. Quiet on failure -- the list is already on screen.
    return onThreadsChanged(() => {
      // Agents too, not only threads: approving a Bot proposal creates a Bot,
      // and a row is drawn from an agent -- a new thread alone would show nothing.
      reload();
      api.threads().then((r) => setThreads(r.threads || [])).catch(() => {});
    });
  }, [reload]);

  const waiting = useMemo(() => {
    const ids = new Set();
    for (const a of approvals) if (a.requestedBy?.agentId) ids.add(a.requestedBy.agentId);
    return ids;
  }, [approvals]);

  /**
   * Companions only.
   *
   * A room used to be merged in here, ordered by recency alongside your own
   * conversations -- so a burst of bots talking to each other about a task
   * could outrank a Bot actually waiting on you. Rooms are real, and
   * read-only observation of them is a deliberate feature (see
   * docs/architecture/16), but that observation belongs behind an opt-in
   * (the Rooms screen, from the profile menu), not defaulted into the one
   * list this screen's whole job is to keep trustworthy as "what needs me."
   */
  const rows = useMemo(() => {
    const threadFor = new Map(threads.map((t) => [t.threadId, t]));

    const companionRows = agents.map((agent) => {
      const thread = threadFor.get(dmThreadId(agent.agentId));
      const needsYou = waiting.has(agent.agentId);
      // A pending approval is the server's word and outranks anything the
      // socket last said: an animation must never hide a decision.
      const live = needsYou ? null : presence[agent.agentId];
      const row = {
        key: `agent:${agent.agentId}`,
        kind: 'companion',
        to: `/agents/${agent.agentId}`,
        threadId: dmThreadId(agent.agentId),
        title: agent.name,
        chip: agent.title || '',
        subtitle: agent.role || STATES[agent.state]?.verb || '',
        preview: previewOf(thread),
        action: needsYou ? 'Waiting for your approval' : live?.action || '',
        state: needsYou ? 'approval' : live?.state || agent.state,
        at: thread?.lastActivity || agent.updatedAt || '',
        // Server-derived. A companion with no thread yet has nothing to have
        // missed, so an absent thread is read rather than unread -- otherwise
        // every newly created companion would arrive already shouting.
        unread: Boolean(thread?.unread),
        agent,
      };
      // The meaningful-item kind, if any: a decision or answer owed, a hard
      // failure, or finished work. Ordinary activity leaves this null so it
      // never dresses up as something that needs the operator.
      row.attention = attentionOf(row);
      return row;
    });

    // A room's own approval need is never lost by leaving it out here: a Bot
    // that needs a decision already shows `needsYou` on its own companion row
    // above, room member or not -- `waiting` is keyed by agent, not by thread.
    const all = companionRows;
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? all.filter((r) => `${r.title} ${r.chip} ${r.subtitle}`.toLowerCase().includes(needle))
      : all;

    // Meaningful items first -- a decision or answer owed, then a hard failure
    // to notice, then finished work -- and recency within the same rank. A row
    // that needs nothing keeps its place in the recency-ordered list, so the
    // inbox reads "what needs me, then everything else" rather than burying an
    // approval under a burst of ordinary chatter.
    const rank = (r) => (r.attention != null ? ATTENTION_RANK[r.attention] : 99);
    return filtered.sort((a, b) => (
      rank(a) - rank(b) || String(b.at).localeCompare(String(a.at))
    ));
  }, [agents, threads, waiting, query, presence]);

  const listRef = useRef(null);
  useFlipList(listRef, rows.map((r) => r.key).join('|'));

  // In the order they were pinned, not the list's recency order -- a pin is
  // the operator saying where something lives.
  const pinnedRows = pins.map((id) => rows.find((r) => r.threadId === id)).filter(Boolean);

  // A decision or an answer owed both count as "needs you" for the account dot.
  const needsYouCount = rows.filter((r) => r.attention === 'approval' || r.attention === 'answer').length;
  const unreadCount = rows.filter((r) => r.unread).length;

  // Re-read rather than splice: the server decides the final id and status,
  // and a created Bot brings a thread (and its greeting) with it.
  function created(to) {
    setCreating(false);
    reload();
    api.threads().then((r) => setThreads(r.threads || [])).catch(() => {});
    navigate(to);
  }

  const retry = () => { setFeedError(null); reload(); api.threads().then((r) => setThreads(r.threads || [])).catch(setFeedError); };

  return (
    <div className={`roster${variant === 'pane' ? ' roster--pane' : ''}`}>
      <header className="rs-top">
        {searching ? (
          <>
            <input className="rs-search" type="search" value={query} data-autofocus autoFocus
                   placeholder="Search" aria-label="Search agents and rooms"
                   onChange={(e) => setQuery(e.target.value)} />
            <button type="button" className="rs-cancel"
                    onClick={() => { setSearching(false); setQuery(''); }}>Cancel</button>
          </>
        ) : (
          <>
            <button type="button" className="rs-me" aria-label="Account and settings"
                    onClick={() => setProfile(true)}>
              <UserAvatar size={42} />
              {needsYouCount > 0 && <i className="rs-me-dot" aria-hidden="true" />}
            </button>
            <span className="rs-spacer" />
            <button type="button" className="rs-round" aria-label="Search" onClick={() => setSearching(true)}>
              <Icon name="search" size={21} />
            </button>
            <button type="button" className="rs-round" aria-label="Create" onClick={() => setCreating(true)}>
              <Icon name="plus" size={23} />
            </button>
          </>
        )}
      </header>

      {(error || feedError) && !rows.length && (
        <Problem error={error ? new Error(error) : feedError} fallback={COPY.loadList} onRetry={retry} />
      )}

      {!query && pinnedRows.length > 0 && (
        <ul className="rs-pins" aria-label="Pinned">
          {pinnedRows.map((row) => (
            <li key={row.key}>
              <Link className="rs-pin" to={row.to} data-open={row.to === location.pathname ? 'true' : undefined}>
                <span className="rs-pin-mark">
                  <Mark row={row} size={56} />
                  {(row.attention === 'approval' || row.attention === 'answer')
                    ? <i className="rs-pin-dot rs-pin-dot--warn" aria-hidden="true" />
                    : row.unread && <i className="rs-pin-dot" aria-hidden="true" />}
                </span>
                <span className="rs-pin-name">{row.title}</span>
                {(row.attention === 'approval' || row.attention === 'answer') && <span className="sr-only">Needs you</span>}
                {row.unread && <span className="sr-only">Unread</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}

      {/* A row like any other, first in the list, so the first thing an owner with
          only Engineering sees is somewhere to start. Hidden while searching: it
          is not a result. */}
      {firstRun === OFFER && !query && !error && (
        <FirstBotOffer onCreated={(bot) => created(`/agents/${bot.agentId}`)} />
      )}

      {loading && !rows.length && !error && <RosterSkeleton />}

      {!loading && !error && !feedError && rows.length === 0 && (
        <div className="rs-empty">
          <Companion archetype="pebble" color="#7b93ff" state="idle" size={64} />
          <strong>{query ? 'Nothing matches that' : 'No agents yet'}</strong>
          <span>{query ? 'Try a different name.' : 'Hire your first AI teammate and start a conversation.'}</span>
          {!query && <button type="button" className="primary" onClick={() => navigate('/agents/new')}>New agent</button>}
        </div>
      )}

      <ul className="rs-list" ref={listRef}>
        {rows.map((row) => {
          const line = subline(row);
          return (
            <li key={row.key} data-key={row.key}>
              <Link className="rs-row" to={row.to} data-kind={row.kind} data-state={row.state}
                    data-attention={row.attention || undefined}
                    data-unread={row.unread ? 'true' : undefined}
                    data-open={row.to === location.pathname ? 'true' : undefined}>
                <Mark row={row} size={48} />
                <span className="rs-main">
                  <span className="rs-line">
                    <strong className="rs-name">{row.title}</strong>
                    {row.chip && <span className="rs-chip">{row.chip}</span>}
                    {row.unread && <span className="sr-only">Unread</span>}
                    <time className="rs-time">{timeLabel(row.at)}</time>
                  </span>
                  <span className="rs-line">
                    <span className={`rs-preview${line.live ? ' is-live' : ''}${line.item ? ' is-ask' : ''}`}>
                      {line.text}
                    </span>
                    {/* The state is still said in words for a screen reader: motion
                        alone is the least reliable carrier of it. */}
                    {row.state !== 'idle' && <span className="sr-only">{STATES[row.state]?.label}</span>}
                    {row.unread && <i className="rs-dot" aria-hidden="true" />}
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>

      {creating && <CreateMenu agents={agents} onClose={() => setCreating(false)} onCreated={created} />}
      {profile && <ProfileSheet onClose={() => setProfile(false)} />}
    </div>
  );
}
