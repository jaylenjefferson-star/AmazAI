import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import CreateAgent from '../components/CreateAgent';
import Icon from '../components/Icon';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';

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
           aria-label={mode === 'room' ? 'New room' : 'Create'}>
        {mode === 'menu' ? (
          <>
            <h2 className="sheet-title">Create</h2>
            <button type="button" className="sheet-row" onClick={() => setMode('companion')}>
              <Companion archetype="pebble" color="#2b6bff" state="idle" size={30} />
              <span>
                <strong>New companion</strong>
                <small>Pick a character, give it work and a budget.</small>
              </span>
            </button>
            <button type="button" className="sheet-row" onClick={() => setMode('room')}
                    disabled={!agents.length}>
              <Companion archetype="cloud" color="#12a594" state="idle" size={30} />
              <span>
                <strong>New room</strong>
                <small>{agents.length
                  ? 'Several companions on one task-bound thread.'
                  : 'Create a companion first.'}</small>
              </span>
            </button>
            <button type="button" className="sheet-row" disabled={!agents.length}
                    onClick={() => { onClose(); navigate('/routines/new'); }}>
              <Companion archetype="lantern" color="#f0a93b" state="idle" size={30} />
              <span>
                <strong>New routine</strong>
                <small>{agents.length
                  ? 'Work that runs on its own schedule, whether or not you are here.'
                  : 'Create a companion first.'}</small>
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
            <h2 className="sheet-title">New room</h2>
            <label className="sheet-field">
              <span>What is this room for?</span>
              <input value={title} autoFocus onChange={(e) => setTitle(e.target.value)}
                     placeholder="Ship the console" />
            </label>
            <fieldset className="sheet-field">
              <legend>Who is in it?</legend>
              <div className="sheet-picks">
                {agents.map((a) => {
                  const on = picked.includes(a.agentId);
                  return (
                    <label key={a.agentId} className={`pick-chip ${on ? 'on' : ''}`}>
                      <input type="checkbox" checked={on} onChange={() => setPicked((cur) => (
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
                {busy ? 'Creating…' : 'Create room'}
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
  }, []);

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
      return {
        key: `agent:${agent.agentId}`,
        kind: 'companion',
        to: `/agents/${agent.agentId}`,
        title: agent.name,
        subtitle: agent.role || STATES[agent.state]?.verb || '',
        state: needsYou ? 'approval' : agent.state,
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
          title: room.title || 'Room',
          subtitle: members.length
            ? members.map((m) => m.name).join(', ')
            : 'No companions in this room yet',
          state: needsYou ? 'approval' : (room.status === 'active' ? 'working' : 'idle'),
          at: room.lastActivity || '',
          unread: Boolean(room.unread),
          members,
        };
      });

    const all = [...companionRows, ...roomRows];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? all.filter((r) => `${r.title} ${r.subtitle}`.toLowerCase().includes(needle))
      : all;

    return filtered.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  }, [agents, threads, byId, waiting, query]);

  const needsYouCount = rows.filter((r) => r.state === 'approval').length;
  const unreadCount = rows.filter((r) => r.unread).length;

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
            placeholder="Search companions and rooms"
            aria-label="Search companions and rooms"
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

      {loading && !rows.length && <div className="empty">Opening your inbox…</div>}

      {!loading && !error && rows.length === 0 && (
        <div className="rest-state">
          <Companion archetype="pebble" color="#12a594" state="idle" size={56} />
          <div>
            <strong>{query ? 'Nothing matches that' : 'No companions yet'}</strong>
            <span>{query
              ? 'Try a different name.'
              : 'Create your first companion to start a conversation. Nothing here is pre-filled or simulated.'}</span>
          </div>
        </div>
      )}

      <ul className="inbox-list">
        {rows.map((row) => (
          <li key={row.key}>
            <Link className="inbox-row" to={row.to} data-kind={row.kind}
                  data-unread={row.unread ? 'true' : undefined}
                  data-open={row.to === location.pathname ? 'true' : undefined}>
              <span className="inbox-mark">
                {row.kind === 'room'
                  ? <RoomMark members={row.members} />
                  : <Companion archetype={row.agent.archetype} color={row.agent.color}
                               state={row.state} size={44} name={row.title} />}
              </span>

              <span className="inbox-main">
                <span className="inbox-line">
                  <strong>{row.title}</strong>
                  {row.unread && <span className="sr-only">Unread</span>}
                  <small>{timeLabel(row.at)}</small>
                </span>
                <span className="inbox-line">
                  <span className="inbox-sub">{row.subtitle}</span>
                  <span className={`inbox-state s-${row.state}`}>
                    {STATES[row.state]?.label || 'Idle'}
                  </span>
                  {row.unread && <span className="inbox-dot" aria-hidden="true" />}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>

      {creating && (
        <CreateSheet
          agents={agents}
          onClose={() => setCreating(false)}
          onCreated={(to) => {
            setCreating(false);
            // Re-read rather than splice: the server decides the final id and
            // status, and a created companion brings a thread with it.
            reload();
            api.threads().then((r) => setThreads(r.threads || [])).catch(() => {});
            navigate(to);
          }}
        />
      )}
    </div>
  );
}
