import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import { connect } from './ws';
import { signOut } from './auth';
import Sidebar from './components/Sidebar';
import Timeline from './components/Timeline';
import RightPanel from './components/RightPanel';

export default function App() {
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
  const threadIdRef = useRef(null);

  threadIdRef.current = threadId;

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
      setStreaming(null);
    } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { loadThread(threadId); }, [threadId, loadThread]);

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
        case 'delta':
          setStreaming((s) => ({ author: s?.author, text: (s?.text || '') + ev.text }));
          break;
        case 'tool':
          setItems((i) => [...i, { type: 'tool', name: ev.name, summary: ev.summary }]);
          break;
        case 'approval.requested':
          setApprovals((a) => [...a, ev.approval]);
          setItems((i) => [...i, { type: 'approval', approval: ev.approval }]);
          break;
        case 'run.state':
          setRunCost(ev.costUsd || 0);
          break;
        case 'run.end':
          setStreaming((s) => {
            if (s?.text) setItems((i) => [...i, { type: 'message', role: 'assistant', text: s.text }]);
            return null;
          });
          setRunCost(ev.costUsd || 0);
          setActiveRuns((r) => r.filter((x) => x.runId !== ev.runId));
          break;
        case 'notification':
          setError(ev.message);
          break;
        default:
          break;
      }
    }, setWsStatus);

    return () => sock.close();
  }, []);

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
      <Sidebar
        agents={agents} threads={threads} activeRuns={activeRuns}
        pending={pending} selected={threadId} onSelect={setThreadId}
        onJumpToApproval={(a) => {
          const run = activeRuns.find((r) => r.runId === a.runId);
          if (run) setThreadId(run.threadId);
        }}
        wsStatus={wsStatus}
      />

      <main className="main">
        <div className="threadbar">
          <h2>{currentThread?.title || 'AmazAI'}</h2>
          <span className="meta">
            {running ? '● running' : '○ idle'}
            {runCost > 0 ? ` · $${runCost.toFixed(3)}` : ''}
          </span>
          <span style={{ flex: 1 }} />
          {running && <button onClick={cancel}>Cancel</button>}
          <button onClick={signOut}>Sign out</button>
        </div>

        {error && (
          <div className="err" style={{ margin: '12px 20px 0' }}>
            {error} <button style={{ marginLeft: 8, padding: '1px 6px' }}
                            onClick={() => setError('')}>dismiss</button>
          </div>
        )}

        <Timeline items={items} streaming={streaming}
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
          <button className="primary" disabled={!draft.trim() || !threadId || sending}>
            {sending ? '…' : 'Send'}
          </button>
        </form>
      </main>

      <RightPanel threadId={threadId} agent={agentDetail} onRefreshAgent={loadAgent} />
    </div>
  );
}
