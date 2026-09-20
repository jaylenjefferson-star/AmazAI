import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import CreateAgent from '../components/CreateAgent';
import FirstBotOffer from '../components/FirstBotOffer';
import Icon from '../components/Icon';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';
import { OFFER, useFirstRun } from '../hooks/useFirstRun';
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

// Mirrors `collab.MAX_ROOM_MEMBERS`; the API is the authority.
const MAX_ROOM = 6;

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

/** "You: " only when the last word was yours; a Bot's own line needs no
 *  name, the row already has one. */
function previewOf(thread) {
  if (!thread?.preview) return '';
  return `${thread.previewRole === 'user' ? 'You: ' : ''}${thread.preview}`;
}

// States that mean the Bot is in a turn right now -- what the presence dot and
// the live action line are for. `approval` and `blocked` are attention, not
// activity, and get their own colour instead.
const LIVE = new Set(['thinking', 'working', 'waiting']);

/** Up to three companions, overlapped. A room is legible by who is in it
 *  before its name is read. */
function RoomMark({ members }) {
  const shown = members.slice(0, 3);
  return (
    <span className="inbox-stack" aria-hidden="true">
      {shown.length === 0
        ? <Companion archetype="pebble" color="#8a8f9c" state="offline" size={26} />
        : shown.map((m, i) => (
          <span key={m.agentId} className="inbox-stack-item" style={{ zIndex: shown.length - i }}>
            <Companion archetype={m.archetype} color={m.color} state={m.state} size={26} />
          </span>
        ))}
    </span>
  );
}

/** One Bot (or a room's members), drawn identically in a row and in the pinned
 *  strip. The hover title is the third of the memo's four presence layers --
 *  a line of text for "how much do I need to know" -- and the accessible name
 *  already lives on the companion itself. */
function Mark({ row, size }) {
  const label = STATES[row.state]?.label || 'Idle';
  const title = row.kind === 'room' ? row.title
    : `${row.title} — ${label}${row.action ? `: ${row.action}` : ''}`;
  return (
    <span className="inbox-mark" title={title}>
      {row.kind === 'room'
        ? <RoomMark members={row.members} />
        : <Companion archetype={row.agent.archetype} color={row.agent.color}
                     state={row.state} size={size} name={row.title} />}
      {LIVE.has(row.state) && <i className="inbox-presence" aria-hidden="true" />}
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
  if (row.action && (LIVE.has(row.state) || row.state === 'approval' || row.state === 'blocked')) {
    return { text: row.action, live: true };
  }
  return { text: row.preview || row.subtitle, live: false };
}

/**
 * The create menu.
 *
 * Three entries, not four. An imported companion template is still in the
 * proposed menu, and the control plane serves no route for it -- a fourth
 * entry here could only open a form whose submit has nowhere to go. A menu
 * item that cannot complete is worse than an absent one: it reads as a
 * feature until the moment someone depends on it. It belongs here the day
 * the route does, as the routine entry now does below.
 *
 * A routine navigates away rather than opening inline. Its trigger and
 * schedule need more room than a sheet gives a companion or a room, and
 * `Routines` already owns that form -- reached the same way `/agents/new`
 * reaches `CreateAgent` from the full Agents page.
 */
function CreateSheet({ agents, onClose, onCreated }) {
  const navigate = useNavigate();
  const [mode, setMode] = useState('menu');
  const [title, setTitle] = useState('');
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function createRoom(e) {
    e.preventDefault();
    if (busy || !title.trim() || !picked.length) return;
    setBusy(true);
    setError('');
    try {
      const room = await api.createThread({
        kind: 'room', title: title.trim(), agentIds: picked,
      });
      onCreated(`/rooms/${room.threadId}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  if (mode === 'companion') {
    return (
      <CreateAgent
        onClose={onClose}
        onCreated={(agent) => onCreated(`/agents/${agent.agentId}`)}
      />
    );
  }

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true"
           aria-label={mode === 'room' ? 'New Channel' : 'Create'}>
        {mode === 'menu' ? (
          <>
            <h2 className="sheet-title">Create</h2>
            <button type="button" className="sheet-row" onClick={() => setMode('companion')}>
              <Companion archetype="pebble" color="#2b6bff" state="idle" size={30} />
              <span>
                <strong>New Bot</strong>
                <small>Pick a character, give it a job and a budget.</small>
              </span>
            </button>
            <button type="button" className="sheet-row" onClick={() => setMode('room')}
                    disabled={!agents.length}>
              <Companion archetype="cloud" color="#12a594" state="idle" size={30} />
              <span>
                <strong>New Channel</strong>
                <small>{agents.length
                  ? 'A shared thread for up to six Bots and you.'
                  : 'Create a Bot first.'}</small>
              </span>
            </button>
            <button type="button" className="sheet-row" disabled={!agents.length}
                    onClick={() => { onClose(); navigate('/routines/new'); }}>
              <Companion archetype="lantern" color="#f0a93b" state="idle" size={30} />
              <span>
                <strong>New routine</strong>
                <small>{agents.length
                  ? 'Work that runs on its own schedule, whether or not you are here.'
                  : 'Create a Bot first.'}</small>
              </span>
            </button>
            <p className="sheet-note">
              Companion templates are not offered here yet: importing a
              pre-built companion has no control-plane route, and a form that
              cannot submit is not a feature.
            </p>
          </>
        ) : (
          <form onSubmit={createRoom}>
            <h2 className="sheet-title">New Channel</h2>
            <label className="sheet-field">
              <span>What is this channel for?</span>
              <input value={title} autoFocus onChange={(e) => setTitle(e.target.value)}
                     placeholder="Ship the console" />
            </label>
            <fieldset className="sheet-field">
              <legend>Who is in it? (up to six)</legend>
              <div className="sheet-picks">
                {agents.map((a) => {
                  const on = picked.includes(a.agentId);
                  return (
                    <label key={a.agentId} className={`pick-chip ${on ? 'on' : ''}`}>
                      <input type="checkbox" checked={on}
                             disabled={!on && picked.length >= MAX_ROOM}
                             onChange={() => setPicked((cur) => (
                               on ? cur.filter((x) => x !== a.agentId) : [...cur, a.agentId]
                             ))} />
                      <Companion archetype={a.archetype} color={a.color} state="idle" size={20} />
                      {a.name}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            {error && <div className="err"><span className="msg-text">{error}</span></div>}
            <div className="sheet-actions">
              <button type="button" className="ghost" onClick={() => setMode('menu')}>Back</button>
              <button className="primary" disabled={busy || !title.trim() || !picked.length}>
                {busy ? 'Creating…' : 'Create channel'}
              </button>
            </div>
          </form>
        )}
      </div>
    </>
  );
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
  const [threads, setThreads] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [feedError, setFeedError] = useState('');

  useEffect(() => {
    api.threads().then((r) => setThreads(r.threads || []))
      .catch((e) => setFeedError(e.message));
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

  const byId = useMemo(() => {
    const map = new Map();
    for (const a of agents) map.set(a.agentId, a);
    return map;
  }, [agents]);

  /**
   * Companions and rooms, merged.
   *
   * Recency decides the order, and a companion with no thread yet sorts on
   * nothing rather than on `Date.now()` -- a brand new agent should not
   * outrank the room you were in a minute ago.
   */
  const rows = useMemo(() => {
    const threadFor = new Map(threads.map((t) => [t.threadId, t]));

    const companionRows = agents.map((agent) => {
      const thread = threadFor.get(dmThreadId(agent.agentId));
      const needsYou = waiting.has(agent.agentId);
      // A pending approval is the server's word and outranks anything the
      // socket last said: an animation must never hide a decision.
      const live = needsYou ? null : presence[agent.agentId];
      return {
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
    });

    const roomRows = threads
      .filter((t) => t.kind === 'room')
      .map((room) => {
        const members = (room.agentIds || []).map((id) => byId.get(id)).filter(Boolean);
        const needsYou = members.some((m) => waiting.has(m.agentId));
        return {
          key: `room:${room.threadId}`,
          kind: 'room',
          to: `/rooms/${room.threadId}`,
          threadId: room.threadId,
          title: room.title || 'Room',
          chip: 'Channel',
          preview: previewOf(room),
          action: '',
          subtitle: members.length
            ? members.map((m) => m.name).join(', ')
            : 'No Bots in this channel yet',
          state: needsYou ? 'approval' : (room.status === 'active' ? 'working' : 'idle'),
          at: room.lastActivity || '',
          unread: Boolean(room.unread),
          members,
        };
      });

    const all = [...companionRows, ...roomRows];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? all.filter((r) => `${r.title} ${r.chip} ${r.subtitle}`.toLowerCase().includes(needle))
      : all;

    return filtered.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  }, [agents, threads, byId, waiting, query, presence]);

  // In the order they were pinned, not the list's recency order -- a pin is
  // the operator saying where something lives.
  const pinnedRows = pins.map((id) => rows.find((r) => r.threadId === id)).filter(Boolean);

  const needsYouCount = rows.filter((r) => r.state === 'approval').length;
  const unreadCount = rows.filter((r) => r.unread).length;

  // Re-read rather than splice: the server decides the final id and status,
  // and a created Bot brings a thread (and its greeting) with it.
  function created(to) {
    setCreating(false);
    reload();
    api.threads().then((r) => setThreads(r.threads || [])).catch(() => {});
    navigate(to);
  }

  return (
    <div className={`inbox${variant === 'pane' ? ' inbox--pane' : ''}`}>
      <header className="inbox-head">
        <div className="inbox-head-text">
          <h1>Conversations</h1>
          {/* Two different facts, and the urgent one wins the line: something
              awaiting your decision outranks something merely unseen. */}
          <p>{needsYouCount
            ? `${needsYouCount} waiting on you`
            : unreadCount
              ? `${unreadCount} unread`
              : 'Nothing is waiting on you'}</p>
        </div>
        <div className="inbox-head-actions">
          <button type="button" className="inbox-icon" aria-label="Search"
                  aria-pressed={searching} onClick={() => setSearching((s) => !s)}>
            <Icon name="search" size={19} />
          </button>
          <button type="button" className="inbox-icon" aria-label="Create"
                  onClick={() => setCreating(true)}>
            <Icon name="plus" size={20} />
          </button>
        </div>
      </header>

      {searching && (
        <div className="inbox-search">
          <input
            type="search" value={query} autoFocus
            placeholder="Search Bots and channels"
            aria-label="Search Bots and channels"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      )}

      {error && (
        <div className="empty">
          <strong>Control plane unavailable</strong>
          <span>{error}</span>
        </div>
      )}
      {feedError && !error && (
        <div className="empty">
          <strong>Conversations unavailable</strong>
          <span>{feedError}</span>
        </div>
      )}

      {/* At the top, above the list, so the first thing an owner with nothing
          but Engineering sees is somewhere to start -- not a conversation
          they did not choose. Hidden while searching: it is not a result. */}
      {firstRun === OFFER && !query && !error && (
        <FirstBotOffer onCreated={(bot) => created(`/agents/${bot.agentId}`)} />
      )}

      {loading && !rows.length && <div className="empty">Opening your inbox…</div>}

      {!loading && !error && rows.length === 0 && (
        <div className="rest-state">
          <Companion archetype="pebble" color="#12a594" state="idle" size={56} />
          <div>
            <strong>{query ? 'Nothing matches that' : 'No Bots yet'}</strong>
            <span>{query
              ? 'Try a different name.'
              : 'Create your first Bot to start a conversation. Nothing here is pre-filled or simulated.'}</span>
          </div>
        </div>
      )}

      {!query && pinnedRows.length > 0 && (
        <ul className="pin-strip" aria-label="Pinned">
          {pinnedRows.map((row) => (
            <li key={row.key}>
              <Link className="pin" to={row.to}
                    data-open={row.to === location.pathname ? 'true' : undefined}>
                <span className="pin-mark">
                  <Mark row={row} size={52} />
                  {row.state === 'approval'
                    ? <i className="pin-dot pin-dot--warn" aria-hidden="true" />
                    : row.unread && <i className="pin-dot" aria-hidden="true" />}
                </span>
                <span className="pin-name">{row.title}</span>
                {row.state === 'approval' && <span className="sr-only">Needs you</span>}
                {row.unread && <span className="sr-only">Unread</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}

      <ul className="inbox-list">
        {rows.map((row) => {
          const line = subline(row);
          const idle = row.state === 'idle';
          return (
            <li key={row.key}>
              <Link className="inbox-row" to={row.to} data-kind={row.kind}
                    data-unread={row.unread ? 'true' : undefined}
                    data-open={row.to === location.pathname ? 'true' : undefined}>
                <Mark row={row} size={44} />

                <span className="inbox-main">
                  <span className="inbox-line">
                    <strong>{row.title}</strong>
                    {row.chip && <span className="inbox-chip">{row.chip}</span>}
                    {row.unread && <span className="sr-only">Unread</span>}
                    <small>{timeLabel(row.at)}</small>
                  </span>
                  <span className="inbox-line">
                    <span className={`inbox-sub${line.live ? ' is-live' : ''}`}>{line.text}</span>
                    {/* The label stays for every state that is not rest: motion
                        alone is the least reliable carrier. Idle is announced,
                        not drawn -- a word on every quiet row is noise. */}
                    {idle
                      ? <span className="sr-only">Idle</span>
                      : <span className={`inbox-state s-${row.state}`}>
                          {STATES[row.state]?.label || 'Idle'}
                        </span>}
                    {row.unread && <span className="inbox-dot" aria-hidden="true" />}
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>

      {creating && (
        <CreateSheet
          agents={agents}
          onClose={() => setCreating(false)}
          onCreated={created}
        />
      )}
    </div>
  );
}
