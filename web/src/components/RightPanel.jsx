import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import CoordinationFeed from './CoordinationFeed';

function Activity({ threadId, agents }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!threadId) return;
    api.coordination(threadId).then((r) => setItems(r.coordination || []))
      .catch((e) => setError(e.message));
  }, [threadId]);

  if (error) return <div className="err"><span className="msg-text">{error}</span></div>;
  return <CoordinationFeed items={items} agents={agents} />;
}

function Computer({ threadId, agent }) {
  const [lines, setLines] = useState([]);
  const [cmd, setCmd] = useState('');
  const [history, setHistory] = useState([]);
  const [hIdx, setHIdx] = useState(-1);
  const [busy, setBusy] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    boxRef.current?.scrollTo(0, boxRef.current.scrollHeight);
  }, [lines]);

  async function run(e) {
    e.preventDefault();
    const command = cmd.trim();
    if (!command || busy) return;
    setLines((l) => [...l, `$ ${command}`]);
    setHistory((h) => [command, ...h]);
    setHIdx(-1);
    setCmd('');
    setBusy(true);
    try {
      const r = await api.exec(threadId, command);
      const out = [r.stdout, r.stderr].filter(Boolean).join('\n').trimEnd();
      setLines((l) => [...l, out || '(no output)']);
    } catch (err) {
      setLines((l) => [...l, `error: ${err.message}`]);
    } finally { setBusy(false); }
  }

  function onKey(e) {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.min(hIdx + 1, history.length - 1);
      if (history[next] != null) { setHIdx(next); setCmd(history[next]); }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = hIdx - 1;
      setHIdx(next);
      setCmd(next >= 0 ? history[next] ?? '' : '');
    }
  }

  const ws = agent?.workspace || {};
  const usedMb = Math.round((ws.sessionBytes || 0) / 1e6);
  const pct = Math.min(100, (usedMb / 1000) * 100);

  return (
    <>
      <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 12 }}>
        {agent.name}&rsquo;s own microVM, on its own execution role and its own
        S3 prefix — not a machine any other agent can read or write.
      </div>
      <div className="kv" style={{ marginBottom: 12 }}>
        <span className="k">Mode</span><span className="v">{ws.mode || '—'}</span>
        <span className="k">Storage</span><span className="v">{usedMb} MB / 1 GB</span>
      </div>
      <div className="bar">
        <i className={pct > 80 ? 'warn' : ''} style={{ width: `${pct}%` }} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 14 }}>
        Session storage is discarded after 14 days idle. Anything that matters
        syncs to the drive.
      </div>

      <div className="term" ref={boxRef}>
        {lines.length === 0
          ? `Runs a real shell in ${agent.name}'s own microVM. No model, no tokens, no other agent's files.\nTry: ls /mnt/data/workspace`
          : lines.join('\n')}
      </div>
      <form className="term-input" onSubmit={run}>
        <span>$</span>
        <input value={cmd} onChange={(e) => setCmd(e.target.value)} onKeyDown={onKey}
               placeholder={busy ? 'running…' : 'command'} disabled={busy} />
      </form>
    </>
  );
}

function Usage({ agent }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!agent) return;
    api.usage(agent.agentId).then(setData).catch((e) => setError(e.message));
  }, [agent]);

  if (error) return <div className="err">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;

  const budget = agent.budget || {};
  const pct = budget.perMonthUsd ? (data.totalUsd / budget.perMonthUsd) * 100 : 0;

  return (
    <>
      <div className="kv">
        <span className="k">This month</span><span className="v">${data.totalUsd.toFixed(2)}</span>
        <span className="k">Monthly budget</span><span className="v">${budget.perMonthUsd ?? '—'}</span>
        <span className="k">Per-run cap</span><span className="v">${budget.perRunUsd ?? '—'}</span>
        <span className="k">At ceiling</span><span className="v">{budget.onCeiling || 'hard_stop'}</span>
      </div>
      <div className="bar">
        <i className={pct >= 100 ? 'danger' : pct >= 80 ? 'warn' : ''}
           style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <div className="section-label">Recent runs</div>
      {data.runs.length === 0 && <div className="empty">No runs this month.</div>}
      {data.runs.slice(0, 20).map((r) => (
        <div className="kv" key={r.runId} style={{ marginBottom: 4 }}>
          <span className="k" style={{ fontSize: 11 }}>{r.runId.slice(0, 18)}</span>
          <span className="v">${Number(r.totalUsd || 0).toFixed(3)}</span>
        </div>
      ))}
    </>
  );
}

function Memory({ agent, onChange }) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');

  async function add(e) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    await api.addMemory(agent.agentId, { title: title.trim(), body: body.trim(), pinned: true });
    setTitle(''); setBody('');
    onChange();
  }

  return (
    <>
      {(agent.memory || []).length === 0 && (
        <div className="empty">Nothing remembered yet.</div>
      )}
      {(agent.memory || []).map((m) => (
        <div key={m.memId} style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <strong style={{ flex: 1, fontSize: 13 }}>{m.pinned ? '⚲ ' : ''}{m.title}</strong>
            <button style={{ padding: '2px 6px', fontSize: 11 }}
                    onClick={async () => { await api.deleteMemory(agent.agentId, m.memId); onChange(); }}>
              ✕
            </button>
          </div>
          <div style={{ fontSize: 13 }}>{m.body}</div>
          <div style={{ fontSize: 11, color: 'var(--dim)' }}>
            {m.source === 'user' ? 'added by you' : 'written by the agent'}
            {m.usedCount ? ` · used in ${m.usedCount} runs` : ''}
          </div>
        </div>
      ))}
      <form onSubmit={add} style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 14 }}>
        <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea placeholder="What should this agent remember?" rows={3}
                  value={body} onChange={(e) => setBody(e.target.value)} />
        <button disabled={!title.trim() || !body.trim()}>Add memory</button>
      </form>
    </>
  );
}

function Access({ agent }) {
  const grants = agent.grants || [];
  return (
    <>
      <div className="section-label">Connectors</div>
      {grants.length === 0 && (
        <div className="empty">
          No connectors granted.<br />
          Authorizing a connector grants nothing until you grant it here.
        </div>
      )}
      {grants.map((g) => (
        <div key={g.connectorId} style={{ marginBottom: 14 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <strong style={{ flex: 1, fontSize: 13 }}>{g.connectorId}</strong>
            <span style={{ fontSize: 11, color: 'var(--dim)' }}>{g.capability}</span>
          </div>
          <div style={{ fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--ok)' }}>
            {(g.allowedTools || []).map((t) => `✓ ${t}`).join('  ')}
          </div>
        </div>
      ))}

      <div className="section-label">Built-in tools</div>
      <div style={{ fontSize: 12, fontFamily: 'var(--mono)' }}>
        {(agent.allowedTools || []).map((t) => `✓ ${t}`).join('  ') || '—'}
      </div>
    </>
  );
}

function Skills({ agent, onChange }) {
  const [catalog, setCatalog] = useState([]);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.skills().then((r) => setCatalog(r.skills || [])).catch((e) => setError(e.message));
  }, []);

  const byId = Object.fromEntries(catalog.map((s) => [s.skillId, s]));
  const assigned = agent.skillAssignments || [];
  const assignedIds = new Set(assigned.map((a) => a.skillId));
  const assignable = catalog.filter((s) => s.status === 'active' && !assignedIds.has(s.skillId));

  async function assign() {
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      await api.assignSkill(selected, agent.agentId, byId[selected].currentVersion);
      setSelected('');
      onChange();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function unassign(skillId) {
    setBusy(true);
    setError('');
    try { await api.unassignSkill(skillId, agent.agentId); onChange(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <>
      {error && <div className="err" style={{ marginBottom: 10 }}><span className="msg-text">{error}</span></div>}
      <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 14 }}>
        Assignment, not blanket injection: a skill only enters this agent&rsquo;s
        prompt once it is both active and assigned here, at the version shown.
      </div>
      {assigned.length === 0 && (
        <div className="empty">Nothing assigned. This agent runs on its built-in tools alone.</div>
      )}
      {assigned.map((a) => {
        const skill = byId[a.skillId];
        const stale = skill && skill.status !== 'active';
        return (
          <div key={a.skillId} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
              <strong style={{ flex: 1, fontSize: 13 }}>{skill?.name || a.skillId}</strong>
              <button style={{ padding: '2px 6px', fontSize: 11 }} disabled={busy}
                      onClick={() => unassign(a.skillId)}>✕</button>
            </div>
            <div style={{ fontSize: 11, color: stale ? 'var(--warn)' : 'var(--dim)' }}>
              v{a.version} · {skill?.status || 'unknown'}
              {stale && ' · not currently injected'}
            </div>
          </div>
        );
      })}
      {assignable.length > 0 && (
        <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} style={{ flex: 1 }}>
            <option value="">Assign a skill…</option>
            {assignable.map((s) => (
              <option key={s.skillId} value={s.skillId}>{s.name} · v{s.currentVersion}</option>
            ))}
          </select>
          <button disabled={!selected || busy} onClick={assign}>Assign</button>
        </div>
      )}
    </>
  );
}

const TABS = ['Computer', 'Memory', 'Skills', 'Access', 'Activity', 'Usage'];

export default function RightPanel({ threadId, agent, agents, onRefreshAgent, open, onClose }) {
  const [tab, setTab] = useState('Computer');
  const cls = `task-side ${open ? 'open' : ''}`;
  if (!agent) return <aside className={cls} />;

  return (
    <aside className={cls}>
      <div className="task-side-head">
        <strong>{agent.name}</strong>
        {onClose && <button className="ghost sm" onClick={onClose} aria-label="Close">✕</button>}
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="tabbody">
        {tab === 'Computer' && <Computer threadId={threadId} agent={agent} />}
        {tab === 'Memory' && <Memory agent={agent} onChange={onRefreshAgent} />}
        {tab === 'Skills' && <Skills agent={agent} onChange={onRefreshAgent} />}
        {tab === 'Access' && <Access agent={agent} />}
        {tab === 'Activity' && <Activity threadId={threadId} agents={agents} />}
        {tab === 'Usage' && <Usage agent={agent} />}
      </div>
    </aside>
  );
}
