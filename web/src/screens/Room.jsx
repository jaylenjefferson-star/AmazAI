import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Companion from '../characters/Companion';
import Composer from '../components/Composer';
import Timeline from '../components/Timeline';
import CoordinationFeed from '../components/CoordinationFeed';
import Icon from '../components/Icon';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';
import { threadsChanged } from '../threadsBus';
import { threadToItems } from '../threadItems';

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'expired', 'partial']);

// Mirrors `collab.MAX_ROOM_MEMBERS`; the API is the authority.
const MAX_MEMBERS = 6;

/**
 * Who is in a channel, and who can be added.
 *
 * Every change is a real `PATCH /threads/{id}` -- the API applies the same cap
 * and the same "must be a real, seated Bot" check creating a channel does, and
 * writes a "joined" or "left" line into the conversation.
 */
function Members({ thread, agents, onClose, onChanged }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const ids = thread.agentIds || [];
  const members = ids.map((id) => agents.find((a) => a.agentId === id)).filter(Boolean);
  const addable = agents.filter((a) => !ids.includes(a.agentId) && !['offline'].includes(a.state));

  async function change(next) {
    setBusy(true);
    setError('');
    try {
      await api.patchThread(thread.threadId, { agentIds: next });
      await onChanged();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Members">
        <h2 className="sheet-title">Members <small className="sheet-count">{ids.length} of {MAX_MEMBERS}</small></h2>
        <ul className="member-list">
          {members.map((a) => (
            <li key={a.agentId}>
              <Companion archetype={a.archetype} color={a.color} state="idle" size={30} name={a.name} />
              <span className="member-text"><strong>{a.name}</strong><small>@{a.agentId}</small></span>
              <button type="button" className="ghost sm" disabled={busy || ids.length <= 1}
                      title={ids.length <= 1 ? 'A channel needs at least one Bot' : undefined}
                      onClick={() => change(ids.filter((x) => x !== a.agentId))}>
                Remove
              </button>
            </li>
          ))}
        </ul>
        {ids.length < MAX_MEMBERS ? (
          <label className="sheet-field">
            <span>Add a Bot</span>
            <select id="add-member" value="" disabled={busy || addable.length === 0}
                    onChange={(e) => e.target.value && change([...ids, e.target.value])}>
              <option value="">{addable.length ? 'Choose a Bot…' : 'Every Bot is already here'}</option>
              {addable.map((a) => <option key={a.agentId} value={a.agentId}>{a.name}</option>)}
            </select>
          </label>
        ) : (
          <p className="sheet-note">A channel holds at most {MAX_MEMBERS} Bots.</p>
        )}
        {error && <div className="err"><span className="msg-text">{error}</span></div>}
        <div className="sheet-actions"><button className="primary" onClick={onClose}>Done</button></div>
      </div>
    </>
  );
}

/**
 * A task-bound channel: several Bots, one thread, an owner of record.
 *
 * Two tabs, not one feed, on purpose: the message history is something the
 * owner posts into, and the coordination feed is agents talking to each
 * other about this same task. Merging them would make it look like the
 * owner is a party to hop counts and priority wakes that were never
 * addressed to them.
 *
 * `@bot` in the composer wakes exactly the Bots named, in parallel, each on its
 * own run; a message that names no one goes to the lead. The transcript says who
 * was woken -- a line the API writes, not one drawn here.
 */
export default function Room() {
  const { roomId } = useParams();
  const { agents } = useAgents();
  const [thread, setThread] = useState(null);
  const [items, setItems] = useState([]);
  const [coordination, setCoordination] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [running, setRunning] = useState({});            // runId -> agentId
  const [tab, setTab] = useState('room');
  const [error, setError] = useState('');
  const [membersOpen, setMembersOpen] = useState(false);
  const pollers = useRef({});

  const loadThread = useCallback(() => {
    return api.thread(roomId).then((t) => {
      setThread(t);
      setItems(threadToItems(t.messages));
      // Same rule as a Bot conversation: reading it is what marks it read, and
      // only once the messages actually arrived.
      api.markRead(roomId).then(threadsChanged).catch(() => {});
    }).catch((e) => setError(e.message));
  }, [roomId]);

  useEffect(() => { loadThread(); }, [loadThread]);
  useEffect(() => {
    api.coordination(roomId).then((r) => setCoordination(r.coordination || [])).catch(() => {});
  }, [roomId, items.length]);
  useEffect(() => () => Object.values(pollers.current).forEach(clearInterval), []);

  const members = useMemo(
    () => (thread?.agentIds || []).map((id) => agents.find((a) => a.agentId === id)).filter(Boolean),
    [thread, agents],
  );

  function watch(runId, agentId) {
    setRunning((r) => ({ ...r, [runId]: agentId }));
    clearInterval(pollers.current[runId]);
    pollers.current[runId] = setInterval(async () => {
      try {
        const run = await api.run(runId);
        const state = String(run.state || '').toLowerCase();
        const pending = (run.approvals || []).filter((a) => a.status === 'pending');
        setPendingApprovals((cur) => [...cur.filter((a) => a.runId !== runId), ...pending]);
        if (TERMINAL_STATES.has(state)) {
          clearInterval(pollers.current[runId]);
          setRunning((r) => { const { [runId]: _done, ...rest } = r; return rest; });
          loadThread();
        }
      } catch {
        clearInterval(pollers.current[runId]);
        setRunning((r) => { const { [runId]: _done, ...rest } = r; return rest; });
      }
    }, 1500);
  }

  async function send(text) {
    setItems((current) => [...current, { type: 'message', role: 'user', author: 'you', text, at: new Date().toISOString() }]);
    const result = await api.send(roomId, text);
    (result.runs || [{ runId: result.runId, agentId: null }]).forEach((r) => r.runId && watch(r.runId, r.agentId));
    // The "Woke X and Y" line is written by the API; fetch it.
    loadThread();
  }

  async function stopAll() {
    setError('');
    try {
      await Promise.all(Object.keys(running).map((id) => api.cancel(id)));
    } catch (err) { setError(err.message); }
  }

  async function decide(approval, approve, note) {
    await api.decide(approval.runId, approval.approvalId, approve, note);
    setPendingApprovals((cur) => cur.map((a) => (
      a.approvalId === approval.approvalId ? { ...a, status: approve ? 'approved' : 'denied' } : a
    )));
    if (approve) watch(approval.runId, approval.requestedBy?.agentId);
    loadThread();
  }

  const timelineItems = useMemo(() => [
    ...items,
    ...pendingApprovals.map((approval) => ({ type: 'approval', approval })),
  ], [items, pendingApprovals]);

  const mentionables = useMemo(
    () => members.map((a) => ({ id: a.agentId, name: a.name, archetype: a.archetype, color: a.color })),
    [members],
  );

  // A room is task-bound, so it ends. When it has, the composer goes rather
  // than sitting there disabled: a greyed-out box invites a click and then
  // explains nothing, and the room is still worth reading.
  const statusLabel = thread?.status && thread.status !== 'active' ? thread.status : 'active';
  const readOnly = Boolean(thread?.readOnly) || statusLabel !== 'active';

  if (!thread) return <div className="page"><div className="empty">{error || 'Loading channel…'}</div></div>;

  return (
    <div className="task">
      <div className="task-main">
      {/* The same header a Bot conversation uses. Back goes to the inbox, which
          is where the channel was opened from. The participants are the identity
          here: a channel is recognised by who is in it before its name is read. */}
      <header className="chat-head">
        <Link to="/" className="chat-icon" aria-label="Back to inbox">
          <Icon name="chevronLeft" size={20} />
        </Link>

        <div className="chat-identity">
          <span className="chat-stack" aria-hidden="true">
            {members.slice(0, 3).map((a, i) => (
              <span key={a.agentId} className="chat-stack-item" style={{ zIndex: 3 - i }}>
                <Companion archetype={a.archetype} color={a.color} state={a.state} size={26} />
              </span>
            ))}
          </span>
          <span className="chat-who">
            <strong>{thread.title}</strong>
            <small>{readOnly
              ? `${statusLabel} · read-only`
              : members.map((a) => a.name).join(', ') || 'No Bots yet'}</small>
          </span>
        </div>

        {readOnly ? <span className="chat-icon" aria-hidden="true" /> : (
          <button type="button" className="chat-icon" onClick={() => setMembersOpen(true)}
                  aria-label={`Members (${members.length})`} aria-haspopup="dialog">
            <Icon name="users" size={20} />
          </button>
        )}
      </header>

      <div className="tabs" style={{ margin: '0 16px' }}>
        <button className={tab === 'room' ? 'active' : ''} onClick={() => setTab('room')}>Channel</button>
        <button className={tab === 'coordination' ? 'active' : ''} onClick={() => setTab('coordination')}>
          Activity{coordination.length ? ` (${coordination.length})` : ''}
        </button>
      </div>

      {tab === 'room' ? (
        <>
          {error && <div className="empty"><strong>Something went wrong</strong><span>{error}</span></div>}
          <Timeline items={timelineItems} streaming={null} typing={null} agents={agents}
                    approvals={pendingApprovals} onDecide={decide}
                    mentionIds={mentionables.map((m) => m.id)} />

          {/* Collapsed on purpose. The owner should be able to see that the
              Bots coordinated without the hop counts and priority wakes
              being rendered as messages addressed to them -- which is the
              whole reason Activity is a separate feed. This is the count and
              a way in, not the traffic itself. */}
          {coordination.length > 0 && (
            <button type="button" className="activity-summary"
                    onClick={() => setTab('coordination')}>
              <Icon name="layers" size={18} />
              <span>
                {coordination.length} coordination {coordination.length === 1 ? 'event' : 'events'}
                {' '}between {members.length} Bots
              </span>
              <em>Open</em>
            </button>
          )}
          {readOnly ? (
            <div className="room-closed" role="status">
              <strong>This channel is {statusLabel}</strong>
              <span>Its history stays readable, and the runs it produced keep their
                sealed evidence. Start a new channel to carry the work on.</span>
            </div>
          ) : (
            <Composer name={thread.title} mentionables={mentionables}
                      busy={Object.keys(running).length > 0} canRedirect={false}
                      onSend={send} onStop={stopAll} />
          )}
        </>
      ) : (
        <div className="page" style={{ paddingTop: 8 }}>
          <CoordinationFeed items={coordination} agents={agents} />
        </div>
      )}
      </div>

      {membersOpen && (
        <Members thread={thread} agents={agents}
                 onClose={() => setMembersOpen(false)} onChanged={loadThread} />
      )}
    </div>
  );
}
