import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Companion from '../characters/Companion';
import Timeline from '../components/Timeline';
import CoordinationFeed from '../components/CoordinationFeed';
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

  if (!thread) return <div className="page"><div className="empty">{error || 'Loading room…'}</div></div>;

  return (
    <div className="task">
      <header className="task-head">
        <Link to="/rooms" className="task-back" aria-label="Back to rooms">‹</Link>
        <div className="participants">
          {members.map((a) => (
            <Companion key={a.agentId} archetype={a.archetype} color={a.color}
                       state={a.state} size={30} name={a.name} />
          ))}
        </div>
        <div className="task-who">
          <strong>{thread.title}</strong>
          <span>Owned by {thread.createdBy === 'you' ? 'you' : thread.createdBy} · {thread.status || 'active'}</span>
        </div>
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
          <form className="composer" onSubmit={send}>
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                      placeholder="Message this room… (@agentId to address one directly)"
                      onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(e); } }} />
            <div className="send">
              <button className="primary" disabled={!draft.trim()}>Send</button>
              <span className="hint">⏎ send</span>
            </div>
          </form>
        </>
      ) : (
        <div className="page" style={{ paddingTop: 8 }}>
          <CoordinationFeed items={coordination} agents={agents} />
        </div>
      )}
    </div>
  );
}
