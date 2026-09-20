import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import Icon from '../components/Icon';
import Timeline from '../components/Timeline';
import RightPanel from '../components/RightPanel';
import { api } from '../api';
import { presentAgent, useAgents } from '../hooks/useAgents';

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'expired', 'partial']);

/**
 * One companion, one thread.
 *
 * The approval card and the execution timeline were the strongest parts of
 * the previous three-panel console, and replacing that shell with a
 * mobile-first one would have orphaned both. They live here instead: a
 * focused view you open from Agents, rather than a third panel that a phone
 * has no room for.
 */
export default function Task() {
  const { agentId } = useParams();
  const { agents } = useAgents();
  const [agent, setAgent] = useState(null);
  const [items, setItems] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [runState, setRunState] = useState(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [panelOpen, setPanelOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const pollRef = useRef(null);
  const threadId = `dm-${agentId}`;

  const loadAgent = useCallback(() => {
    api.agent(agentId).then((a) => setAgent(presentAgent(a))).catch((e) => setError(e.message));
  }, [agentId]);

  const loadThread = useCallback(() => {
    api.thread(threadId).then((thread) => {
      setItems((thread.messages || []).map((message) => ({
        type: 'message', role: message.role, author: message.author, text: message.text,
      })));
      // Opening a conversation is reading it. Marked after the messages are
      // in hand rather than on mount, so a thread whose load failed is not
      // recorded as seen. Failure here is silent on purpose: the reader has
      // the conversation, and an error about a read marker would be noise
      // about something they did not ask for.
      api.markRead(threadId).catch(() => {});
    }).catch((e) => {
      // A newly provisioned agent has no conversation yet; a missing thread
      // is not a substitute for demo conversation history.
      if (!String(e.message).includes('404')) setError(e.message);
    });
  }, [threadId]);

  useEffect(() => { loadAgent(); loadThread(); }, [loadAgent, loadThread]);

  // Runs that are still in flight, so a run left mid-approval from an
  // earlier visit still shows a typing indicator and its approval card
  // rather than looking finished.
  useEffect(() => {
    api.approvals('pending').then((r) => {
      setPendingApprovals((r.approvals || []).filter((a) => a.requestedBy?.agentId === agentId));
    }).catch(() => {});
  }, [agentId]);

  const stopPolling = useCallback(() => {
    clearInterval(pollRef.current);
    pollRef.current = null;
    setRunState(null);
  }, []);

  const pollRun = useCallback((runId) => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const run = await api.run(runId);
        // The control plane stores canonical state values as uppercase enum
        // strings (for example, `COMPLETED`), while this view uses lowercase
        // presentation states. Normalize at the boundary so a completed run
        // refreshes its persisted assistant message instead of looking like
        // it is still thinking indefinitely.
        const state = String(run.state || '').toLowerCase();
        setRunState(state);
        const runApprovals = (run.approvals || []).filter((a) => a.status === 'pending');
        setPendingApprovals((current) => {
          const others = current.filter((a) => a.runId !== runId);
          return [...others, ...runApprovals];
        });
        if (TERMINAL_STATES.has(state) || runApprovals.length > 0) {
          if (TERMINAL_STATES.has(state)) stopPolling();
          if (state === 'completed' || state === 'partial') loadThread();
        }
      } catch {
        stopPolling();
      }
    }, 1500);
  }, [loadThread, stopPolling]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  async function send(e) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !agent) return;
    setDraft('');
    setItems((current) => [...current, { type: 'message', role: 'user', author: 'you', text }]);
    try {
      const { runId } = await api.send(threadId, text);
      if (runId) pollRun(runId);
    } catch (err) {
      setError(err.message);
    }
  }

  async function decide(approval, approve, note) {
    await api.decide(approval.runId, approval.approvalId, approve, note);
    setPendingApprovals((current) => current.map((a) => (
      a.approvalId === approval.approvalId ? { ...a, status: approve ? 'approved' : 'denied' } : a
    )));
    loadThread();
  }

  const timelineItems = useMemo(() => [
    ...items,
    ...pendingApprovals.map((approval) => ({ type: 'approval', approval })),
  ], [items, pendingApprovals]);

  const typing = runState && !TERMINAL_STATES.has(runState) && pendingApprovals.every((a) => a.status !== 'pending')
    ? { name: agent?.name, verb: (STATES.thinking || STATES.working).verb }
    : null;

  if (!agent) return <div className="page"><div className="empty">{error || 'Loading companion…'}</div></div>;

  return (
    <div className="task">
      {/* Back goes to the inbox, which is where this conversation was opened
          from now that the inbox is home -- `/agents` was the old section
          list and returning there loses the thread you came in on.

          The identity sits centred between two equal-width controls rather
          than left-aligned beside them, so it stays centred whatever the
          name's length, and the status reads as the companion's own rather
          than as a chip parked at the end of a row. */}
      <header className="chat-head">
        <Link to="/" className="chat-icon" aria-label="Back to inbox">
          <Icon name="chevronLeft" size={20} />
        </Link>

        <div className="chat-identity">
          <Companion archetype={agent.archetype} color={agent.color}
                     state={typing ? 'thinking' : agent.state} size={30} name={agent.name} />
          <span className="chat-who">
            <strong>{agent.name}</strong>
            <small className={`cc-tone-${(STATES[typing ? 'thinking' : agent.state] || STATES.idle).tone}`}>
              {(STATES[typing ? 'thinking' : agent.state] || STATES.idle).label}
            </small>
          </span>
        </div>

        <button type="button" className="chat-icon" onClick={() => setMenuOpen(true)}
                aria-label={`More about ${agent.name}`} aria-haspopup="menu">
          <Icon name="more" size={20} />
        </button>
      </header>

      {error && <div className="empty"><strong>Message not sent</strong><span>{error}</span></div>}
      <Timeline items={timelineItems} streaming={null} typing={typing} agents={agents}
                approvals={pendingApprovals} onDecide={decide} />

      <form className="composer" onSubmit={send}>
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                  placeholder={`Ask ${agent.name} for something…`}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(e); }
                  }} />
        <div className="send">
          <button className="primary" disabled={!draft.trim()}>Send</button>
          <span className="hint">⏎ send</span>
        </div>
      </form>

      {/* The overflow: the two things a conversation leads to. Settings is a
          screen because it is long; the computer stays a panel because it is
          read beside the conversation, not instead of it. */}
      {menuOpen && (
        <>
          <div className="scrim" onClick={() => setMenuOpen(false)} />
          <div className="sheet" role="menu" aria-label={`${agent.name} options`}>
            <h2 className="sheet-title">{agent.name}</h2>
            <Link className="sheet-row" role="menuitem" to={`/agents/${agentId}/settings`}>
              <Companion archetype={agent.archetype} color={agent.color} state="idle" size={30} />
              <span>
                <strong>Companion settings</strong>
                <small>Identity, instructions, model, budget and hours.</small>
              </span>
            </Link>
            <button type="button" className="sheet-row" role="menuitem"
                    onClick={() => { setMenuOpen(false); setPanelOpen(true); }}>
              <Icon name="layers" size={26} />
              <span>
                <strong>Computer and activity</strong>
                <small>Its drive, memory, skills and what it has spent.</small>
              </span>
            </button>
          </div>
        </>
      )}

      <RightPanel threadId={threadId} agent={agent} agents={agents}
                  onRefreshAgent={loadAgent} open={panelOpen}
                  onClose={() => setPanelOpen(false)} />
    </div>
  );
}
