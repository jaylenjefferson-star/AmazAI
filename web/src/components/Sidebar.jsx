const STATE_COLOR = {
  running: 'var(--accent)',
  waiting: 'var(--warn)',
  idle: 'var(--dim)',
  disabled: '#4a5260',
};

function elapsed(startedAt) {
  const s = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}`;
}

export default function Sidebar({
  agents, threads, activeRuns, pending, selected, onSelect, onJumpToApproval,
  wsStatus,
}) {
  const agentState = (agent) => {
    if (agent.state !== 'active') return 'disabled';
    if (pending.some((a) => a.agentId === agent.agentId)) return 'waiting';
    if (activeRuns.some((r) => r.agentId === agent.agentId)) return 'running';
    return 'idle';
  };

  return (
    <aside className="sidebar">
      {pending.length > 0 && (
        <button className="needsyou" onClick={() => onJumpToApproval(pending[0])}>
          <span>⚠</span>
          <span style={{ flex: 1 }}>Needs you</span>
          <span>{pending.length}</span>
        </button>
      )}

      <div className="section-label">Agents</div>
      {agents.length === 0 && <div className="empty">No agents yet.<br />Run provision_agents.py</div>}
      {agents.map((a) => {
        const thread = threads.find((t) => (t.agentIds || []).includes(a.agentId));
        return (
          <button key={a.agentId}
                  className={`row ${selected === thread?.threadId ? 'active' : ''}`}
                  onClick={() => thread && onSelect(thread.threadId)}>
            <span className="dot" style={{ background: STATE_COLOR[agentState(a)] }} />
            <span className="name">{a.name}</span>
          </button>
        );
      })}

      {activeRuns.length > 0 && (
        <>
          <div className="section-label">Active runs</div>
          {activeRuns.map((r) => (
            <button key={r.runId} className="row" onClick={() => onSelect(r.threadId)}>
              <span className="name">{r.agentId}</span>
              <span className="meta">{elapsed(r.startedAt)}</span>
            </button>
          ))}
        </>
      )}

      <div className="section-label">Tasks</div>
      {threads.filter((t) => t.kind !== 'dm').map((t) => (
        <button key={t.threadId}
                className={`row ${selected === t.threadId ? 'active' : ''}`}
                onClick={() => onSelect(t.threadId)}>
          <span className="name">{t.title}</span>
        </button>
      ))}

      <div className="section-label">Connection</div>
      <div className="row" style={{ cursor: 'default' }}>
        <span className="dot" style={{
          background: wsStatus === 'connected' ? 'var(--ok)' : 'var(--warn)',
        }} />
        <span className="meta">{wsStatus}</span>
      </div>
    </aside>
  );
}
