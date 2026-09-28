import AgentAvatar from './AgentAvatar';

const STATE = {
  running:  { color: 'var(--accent)', label: 'running' },
  waiting:  { color: 'var(--warn)',   label: 'needs you' },
  idle:     { color: 'var(--faint)',  label: 'idle' },
  disabled: { color: 'var(--disabled)', label: 'disabled' },
};

function elapsed(startedAt) {
  const s = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}`;
}

export default function Sidebar({
  agents, threads, activeRuns, pending, selected, onSelect, onJumpToApproval,
  onCreate, open, onClose,
}) {
  const agentState = (agent) => {
    if (agent.state !== 'active') return 'disabled';
    if (pending.some((a) => a.agentId === agent.agentId)) return 'waiting';
    if (activeRuns.some((r) => r.agentId === agent.agentId)) return 'running';
    return 'idle';
  };

  const tasks = threads.filter((t) => t.kind !== 'dm');

  return (
    <aside className={`sidebar ${open ? 'open' : ''}`}>
      {pending.length > 0 && (
        <button className="needsyou" onClick={() => onJumpToApproval(pending[0])}>
          <span aria-hidden="true">⚠</span>
          <span style={{ flex: 1 }}>
            {pending.length === 1 ? 'An agent needs you' : 'Agents need you'}
          </span>
          <span className="badge">{pending.length}</span>
        </button>
      )}

      <div className="section-label">
        Agents <span className="count">{agents.length || ''}</span>
        <button className="ghost sm add" onClick={onCreate}
                title="New agent" aria-label="New agent">+</button>
      </div>

      {agents.length === 0 && (
        <div className="empty" style={{ padding: '16px 8px' }}>
          <span className="title">No agents yet</span>
          <span>Make the first one.</span>
          <button className="primary sm" onClick={onCreate}>New agent</button>
        </div>
      )}

      {agents.map((a) => {
        const thread = threads.find((t) => (t.agentIds || []).includes(a.agentId));
        const state = agentState(a);
        const run = activeRuns.find((r) => r.agentId === a.agentId);
        return (
          <button key={a.agentId}
                  className={`row ${selected === thread?.threadId ? 'active' : ''}`}
                  title={`${a.name} — ${STATE[state].label}`}
                  onClick={() => { if (thread) { onSelect(thread.threadId); onClose?.(); } }}>
            <AgentAvatar shape={a.avatar?.shape} color={a.avatar?.color || a.accent}
                         size={18} name={a.name} />
            <span className="name">{a.name}</span>
            <span className={`dot ${state === 'running' ? 'pulse' : ''}`}
                  style={{ background: STATE[state].color, color: STATE[state].color }} />
            {run && <span className="meta">{elapsed(run.startedAt)}</span>}
            {state === 'waiting' && <span className="meta" style={{ color: 'var(--warn)' }}>⚠</span>}
          </button>
        );
      })}

      {tasks.length > 0 && (
        <>
          <div className="section-label">
            Tasks <span className="count">{tasks.length}</span>
          </div>
          {tasks.map((t) => (
            <button key={t.threadId}
                    className={`row ${selected === t.threadId ? 'active' : ''}`}
                    onClick={() => { onSelect(t.threadId); onClose?.(); }}>
              <span className="name">{t.title}</span>
            </button>
          ))}
        </>
      )}
    </aside>
  );
}
