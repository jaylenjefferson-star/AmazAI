import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import { connect } from './ws';
import { startLogout, useAuth0 } from './auth0';
import Topbar from './components/Topbar';
import Sidebar from './components/Sidebar';
import Timeline from './components/Timeline';
import RightPanel from './components/RightPanel';
import CreateAgent from './components/CreateAgent';

const REGION = import.meta.env.VITE_REGION || 'us-west-2';

export default function App() {
  const { logout } = useAuth0();
  const [agents, setAgents] = useState([]);
  const [threads, setThreads] = useState([]);
  const [threadId, setThreadId] = useState(null);
  const [items, setItems] = useState([]);
  const [streaming, setStreaming] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [activeRuns, setActiveRuns] = useState([]);
  const [agentDetail, setAgentDetail] = useState(null);
  const [wsStatus, setWsStatus] = useState('connecting');
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [runCost, setRunCost] = useState(0);
  const [spend, setSpend] = useState(0);
  const [creating, setCreating] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const threadIdRef = useRef(null);
  const agentsRef = useRef([]);
  const streamRef = useRef(null);

  threadIdRef.current = threadId;
  agentsRef.current = agents;

  // --- initial load ------------------------------------------------------
  useEffect(() => {
    (async () => {
      try {
        const [a, t] = await Promise.all([api.agents(), api.threads()]);
        setAgents(a.agents || []);
        setThreads(t.threads || []);
        if (!threadIdRef.current && t.threads?.length) setThreadId(t.threads[0].threadId);
      } catch (e) { setError(e.message); }
    })();
  }, []);

  // Month-to-date spend is the sum of the per-agent ledgers; there is no
  // account-wide rollup endpoint and inventing one would put a second source
  // of truth next to the ledger. Refreshed whenever a run ends.
  const loadSpend = useCallback(async () => {
    const list = agentsRef.current;
    if (!list.length) return;
    try {
      const rows = await Promise.all(
        list.map((a) => api.usage(a.agentId).catch(() => null)),
      );
      setSpend(rows.reduce((sum, r) => sum + (r?.totalUsd || 0), 0));
    } catch { /* the ledger is informational here; never block the console */ }
  }, []);

  useEffect(() => { loadSpend(); }, [agents, loadSpend]);

  const budget = useMemo(
    () => agents.reduce((sum, a) => sum + (a.budget?.perMonthUsd || 0), 0),
    [agents],
  );

  const currentThread = useMemo(
    () => threads.find((t) => t.threadId === threadId),
    [threads, threadId],
  );

  const currentAgentId = currentThread?.agentIds?.[0];

  const loadAgent = useCallback(async () => {
    if (!currentAgentId) return setAgentDetail(null);
    try { setAgentDetail(await api.agent(currentAgentId)); }
    catch (e) { setError(e.message); }
  }, [currentAgentId]);

  useEffect(() => { loadAgent(); }, [loadAgent]);

  // --- thread contents ---------------------------------------------------
  const loadThread = useCallback(async (id) => {
    if (!id) return;
    try {
      const t = await api.thread(id);
      setItems((t.messages || []).map((m) => ({
        type: 'message', role: m.role, author: m.author, text: m.text,
      })));
      streamRef.current = null;
      setStreaming(null);
    } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { loadThread(threadId); }, [threadId, loadThread]);

  /**
   * Commit whatever is mid-stream, then append `extra`.
   *
   * A tool call or an approval that arrives while the model is still talking
   * has to land *after* the prose leading up to it. Streaming text is not an
   * item until it is flushed, so appending straight to `items` would file the
   * approval above the sentence explaining why it was asked for.
   */
  const flush = useCallback((extra) => {
    const s = streamRef.current;
    streamRef.current = null;
    setStreaming(null);
    setItems((i) => {
      const base = s?.text
        ? [...i, { type: 'message', role: 'assistant', author: s.author, text: s.text }]
        : i;
      return extra ? [...base, extra] : base;
    });
  }, []);

  // --- live socket -------------------------------------------------------
  useEffect(() => {
    const sock = connect((ev) => {
      // Ignore traffic for threads that are not on screen.
      if (ev.threadId && ev.threadId !== threadIdRef.current) {
        if (ev.type === 'approval.requested') {
          setApprovals((a) => [...a, ev.approval]);
        }
        return;
      }

      switch (ev.type) {
        case 'delta': {
          const next = {
            author: streamRef.current?.author || ev.author,
            text: (streamRef.current?.text || '') + ev.text,
          };
          streamRef.current = next;
          setStreaming(next);
          break;
        }
        case 'tool':
          flush({ type: 'tool', name: ev.name, summary: ev.summary });
          break;
        case 'handoff':
          flush({ type: 'handoff', handoff: ev.handoff });
          break;
        case 'approval.requested':
          setApprovals((a) => [...a, ev.approval]);
          flush({ type: 'approval', approval: ev.approval });
          break;
        case 'run.state':
          setRunCost(ev.costUsd || 0);
          break;
        case 'run.end':
          flush();
          setRunCost(ev.costUsd || 0);
          setActiveRuns((r) => r.filter((x) => x.runId !== ev.runId));
          loadSpend();
          break;
        case 'notification':
          setError(ev.message);
          break;
        default:
          break;
      }
    }, setWsStatus);

    return () => sock.close();
  }, [loadSpend, flush]);

  // --- actions -----------------------------------------------------------
  async function send(e) {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || !threadId || sending) return;
    setDraft('');
    setItems((i) => [...i, { type: 'message', role: 'user', author: 'you', text }]);
    setSending(true);
    try {
      const r = await api.send(threadId, text);
      setActiveRuns((runs) => [...runs, {
        runId: r.runId, threadId, agentId: currentAgentId, startedAt: new Date().toISOString(),
      }]);
    } catch (e2) { setError(e2.message); }
    finally { setSending(false); }
  }

  async function decide(approval, approve, note) {
    try {
      await api.decide(approval.runId, approval.approvalId, approve, note);
      setApprovals((a) => a.map((x) => x.approvalId === approval.approvalId
        ? { ...x, status: approve ? 'approved' : 'denied', note } : x));
    } catch (e) { setError(e.message); }
  }

  async function cancel() {
    const run = activeRuns.find((r) => r.threadId === threadId);
    if (!run) return;
    try { await api.cancel(run.runId); } catch (e) { setError(e.message); }
  }

  const pending = approvals.filter((a) => a.status === 'pending');
  const running = activeRuns.some((r) => r.threadId === threadId);

  return (
    <div className="app">
      <Topbar
        region={REGION} spend={spend} budget={budget} wsStatus={wsStatus}
        onSignOut={() => startLogout(logout)}
        onToggleSidebar={() => setNavOpen((o) => !o)}
        onTogglePanel={() => setPanelOpen((o) => !o)}
      />

      <Sidebar
        agents={agents} threads={threads} activeRuns={activeRuns}
        pending={pending} selected={threadId} onSelect={setThreadId}
        onJumpToApproval={(a) => {
          const run = activeRuns.find((r) => r.runId === a.runId);
          if (run) setThreadId(run.threadId);
        }}
        onCreate={() => { setCreating(true); setNavOpen(false); }}
        open={navOpen} onClose={() => setNavOpen(false)}
      />

      <main className="main">
        <div className="threadbar">
          <h2>{currentThread?.title || 'AmazAI'}</h2>
          <span className={`state-pill ${running ? 'running' : ''}`}>
            <span className={`dot ${running ? 'pulse' : ''}`}
                  style={{ background: running ? 'var(--accent)' : 'var(--faint)',
                           color: 'var(--accent)' }} />
            {running ? 'running' : 'idle'}
          </span>
          {runCost > 0 && <span className="meta">${runCost.toFixed(3)} this run</span>}
          <span style={{ flex: 1 }} />
          {running && <button className="sm" onClick={cancel}>Cancel run</button>}
        </div>

        {error && (
          <div className="err" style={{ margin: '12px 20px 0' }}>
            <span className="msg-text">{error}</span>
            <button className="ghost sm" onClick={() => setError('')}>Dismiss</button>
          </div>
        )}

        <Timeline items={items} streaming={streaming} agents={agents}
                  approvals={approvals} onDecide={decide} />

        <form className="composer" onSubmit={send}>
          <textarea
            value={draft} onChange={(e) => setDraft(e.target.value)}
            placeholder={currentThread ? 'Describe a task…' : 'No thread selected'}
            disabled={!threadId}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
            }}
          />
          <div className="send">
            <button className="primary" disabled={!draft.trim() || !threadId || sending}>
              {sending ? '…' : 'Send'}
            </button>
            <span className="hint">⏎ send</span>
          </div>
        </form>
      </main>

      {creating && (
        <CreateAgent
          onClose={() => setCreating(false)}
          onCreated={async (agent) => {
            setCreating(false);
            // Re-read rather than splice the response in: the server decides
            // the final id and status, and a created agent brings a thread
            // with it.
            try {
              const [a, t] = await Promise.all([api.agents(), api.threads()]);
              setAgents(a.agents || []);
              setThreads(t.threads || []);
              const dm = (t.threads || []).find(
                (x) => (x.agentIds || []).includes(agent.agentId));
              if (dm) setThreadId(dm.threadId);
            } catch (e) { setError(e.message); }
          }}
        />
      )}

      {(navOpen || panelOpen) && (
        <div className="scrim" onClick={() => { setNavOpen(false); setPanelOpen(false); }} />
      )}

      <RightPanel threadId={threadId} agent={agentDetail} onRefreshAgent={loadAgent}
                  open={panelOpen} />
    </div>
  );
}
