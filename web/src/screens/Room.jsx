import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Companion from '../characters/Companion';
import Timeline from '../components/Timeline';
import CoordinationFeed from '../components/CoordinationFeed';
import Icon from '../components/Icon';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'expired', 'partial']);

/**
 * A task-bound room: several companions, one thread, an owner of record.
 *
 * Two tabs, not one feed, on purpose: the message history is something the
 * owner posts into, and the coordination feed is agents talking to each
 * other about this same task. Merging them would make it look like the
 * owner is a party to hop counts and priority wakes that were never
 * addressed to them.
 */
export default function Room() {
  const { roomId } = useParams();
  const { agents } = useAgents();
  const [thread, setThread] = useState(null);
  const [items, setItems] = useState([]);
  const [coordination, setCoordination] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [tab, setTab] = useState('room');
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');

  const loadThread = useCallback(() => {
    api.thread(roomId).then((t) => {
      setThread(t);
      setItems((t.messages || []).map((m) => ({
        type: 'message', role: m.role, author: m.author, text: m.text,
      })));
      // Same rule as a companion conversation: reading it is what marks it
      // read, and only once the messages actually arrived.
      api.markRead(roomId).catch(() => {});
    }).catch((e) => setError(e.message));
  }, [roomId]);

  useEffect(() => { loadThread(); }, [loadThread]);
  useEffect(() => {
    api.coordination(roomId).then((r) => setCoordination(r.coordination || [])).catch(() => {});
  }, [roomId, items.length]);

  const members = useMemo(
    () => (thread?.agentIds || []).map((id) => agents.find((a) => a.agentId === id)).filter(Boolean),
    [thread, agents],
  );

  async function send(e) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft('');
    setItems((current) => [...current, { type: 'message', role: 'user', author: 'you', text }]);
    try {
      const { runId } = await api.send(roomId, text);
      if (runId) pollForApprovals(runId);
    } catch (err) {
      setError(err.message);
    }
  }

  function pollForApprovals(runId) {
    const iv = setInterval(async () => {
      try {
        const run = await api.run(runId);
        const runApprovals = (run.approvals || []).filter((a) => a.status === 'pending');
        setPendingApprovals((cur) => [...cur.filter((a) => a.runId !== runId), ...runApprovals]);
        if (TERMINAL_STATES.has(run.state)) {
          clearInterval(iv);
          loadThread();
        }
      } catch { clearInterval(iv); }
    }, 1500);
  }

  async function decide(approval, approve, note) {
    await api.decide(approval.runId, approval.approvalId, approve, note);
    setPendingApprovals((cur) => cur.map((a) => (
      a.approvalId === approval.approvalId ? { ...a, status: approve ? 'approved' : 'denied' } : a
    )));
    loadThread();
  }

  const timelineItems = useMemo(() => [
    ...items,
    ...pendingApprovals.map((approval) => ({ type: 'approval', approval })),
  ], [items, pendingApprovals]);

  // A room is task-bound, so it ends. When it has, the composer goes rather
  // than sitting there disabled: a greyed-out box invites a click and then
  // explains nothing, and the room is still worth reading.
  const statusLabel = thread?.status && thread.status !== 'active' ? thread.status : 'active';
  const readOnly = Boolean(thread?.readOnly) || statusLabel !== 'active';

  if (!thread) return <div className="page"><div className="empty">{error || 'Loading room…'}</div></div>;

  return (
    <div className="task">
      {/* The same header a companion conversation uses. Back goes to the
          inbox, which is where the room was opened from -- `/rooms` is the
          old section list. The participants are the identity here: a room is
          recognised by who is in it before its name is read. */}
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
              : members.map((a) => a.name).join(', ') || 'No companions yet'}</small>
          </span>
        </div>

        <span className="chat-icon" aria-hidden="true" />
      </header>

      <div className="tabs" style={{ margin: '0 16px' }}>
        <button className={tab === 'room' ? 'active' : ''} onClick={() => setTab('room')}>Room</button>
        <button className={tab === 'coordination' ? 'active' : ''} onClick={() => setTab('coordination')}>
          Activity{coordination.length ? ` (${coordination.length})` : ''}
        </button>
      </div>

      {tab === 'room' ? (
        <>
          {error && <div className="empty"><strong>Message not sent</strong><span>{error}</span></div>}
          <Timeline items={timelineItems} streaming={null} typing={null} agents={agents}
                    approvals={pendingApprovals} onDecide={decide} />

          {/* Collapsed on purpose. The owner should be able to see that the
              companions coordinated without the hop counts and priority wakes
              being rendered as messages addressed to them -- which is the
              whole reason Activity is a separate feed. This is the count and
              a way in, not the traffic itself. */}
          {coordination.length > 0 && (
            <button type="button" className="activity-summary"
                    onClick={() => setTab('coordination')}>
              <Icon name="layers" size={18} />
              <span>
                {coordination.length} coordination {coordination.length === 1 ? 'event' : 'events'}
                {' '}between {members.length} companions
              </span>
              <em>Open</em>
            </button>
          )}
          {readOnly ? (
            <div className="room-closed" role="status">
              <strong>This room is {statusLabel}</strong>
              <span>Its history stays readable, and the runs it produced keep their
                sealed evidence. Start a new room to carry the work on.</span>
            </div>
          ) : (
            <form className="composer" onSubmit={send}>
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                        placeholder="Message this room… (@agentId to address one directly)"
                        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(e); } }} />
              <div className="send">
                <button className="primary" disabled={!draft.trim()}>Send</button>
                <span className="hint">⏎ send</span>
              </div>
            </form>
          )}
        </>
      ) : (
        <div className="page" style={{ paddingTop: 8 }}>
          <CoordinationFeed items={coordination} agents={agents} />
        </div>
      )}
    </div>
  );
}
