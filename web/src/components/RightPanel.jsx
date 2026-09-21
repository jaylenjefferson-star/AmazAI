import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import Companion from '../characters/Companion';
import CoordinationFeed from './CoordinationFeed';
import Icon from './Icon';
import RoutineList from './RoutineList';

export function Activity({ threadId, agents }) {
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

export function Computer({ threadId, agent }) {
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

export function Usage({ agent }) {
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

/**
 * What a Bot knows, and the way to fix it when it is wrong.
 *
 * Two lists, kept apart on purpose: what *this* Bot remembers, and what every
 * Bot is told about you (shared). A correction edits the fact in place and says
 * so -- "corrected by you" beside "written by the agent" -- because a wrong
 * memory that is silently fixed is one nobody learns to trust or distrust. The
 * API refuses to let an edit move a fact between the two, so correcting one can
 * never quietly publish it to everyone.
 */
export function Memory({ agent, onChange }) {
  const [scope, setScope] = useState('agent');
  const [shared, setShared] = useState(null);
  const [editing, setEditing] = useState(null);       // { memId, title, body, kind }
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (scope !== 'shared') return;
    api.sharedMemory().then((r) => setShared(r.memory || [])).catch((e) => setError(e.message));
  }, [scope]);

  const rows = scope === 'shared'
    ? (shared || []).filter((m) => m.status !== 'revoked')
    : (agent.memory || []).filter((m) => m.status !== 'revoked');

  async function refresh() {
    if (scope === 'shared') setShared((await api.sharedMemory()).memory || []);
    else onChange();
  }

  async function add(e) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    setError('');
    try {
      const entry = { title: title.trim(), body: body.trim(), pinned: true };
      if (scope === 'shared') await api.addSharedMemory(entry);
      else await api.addMemory(agent.agentId, entry);
      setTitle(''); setBody('');
      await refresh();
    } catch (err) { setError(err.message); }
  }

  async function saveEdit() {
    setBusy(true);
    setError('');
    try {
      const changes = { title: editing.title, body: editing.body, kind: editing.kind };
      if (scope === 'shared') await api.updateSharedMemory(editing.memId, changes);
      else await api.updateMemory(agent.agentId, editing.memId, changes);
      setEditing(null);
      await refresh();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  async function remove(m) {
    setError('');
    try {
      if (scope === 'shared') await api.deleteSharedMemory(m.memId);
      else await api.deleteMemory(agent.agentId, m.memId);
      await refresh();
    } catch (err) { setError(err.message); }
  }

  return (
    <>
      <div className="seg" role="tablist" aria-label="Whose memory">
        {[['agent', `${agent.name}`], ['shared', 'Shared']].map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={scope === key}
                  className={scope === key ? 'on' : ''} onClick={() => { setScope(key); setEditing(null); }}>
            {label}
          </button>
        ))}
      </div>
      <p className="pane-note">
        {scope === 'shared'
          ? 'Facts every Bot is told about you. Nothing reaches this list without your say-so.'
          : `What ${agent.name} remembers on its own. Correct anything that is wrong.`}
      </p>
      {error && <div className="err" style={{ marginBottom: 10 }}><span className="msg-text">{error}</span></div>}

      {rows.length === 0 && <div className="empty">Nothing remembered yet.</div>}
      {rows.map((m) => (
        <div key={m.memId} className="mem-row">
          {editing?.memId === m.memId ? (
            <div className="mem-edit">
              <input aria-label="Title" value={editing.title}
                     onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
              <textarea aria-label="What is remembered" rows={3} value={editing.body}
                        onChange={(e) => setEditing({ ...editing, body: e.target.value })} />
              <select aria-label="Kind" value={editing.kind}
                      onChange={(e) => setEditing({ ...editing, kind: e.target.value })}>
                <option value="foundational">Always in context</option>
                <option value="note">Short-lived note</option>
                <option value="log">Dated history</option>
              </select>
              <div className="mem-actions">
                <button className="primary sm" disabled={busy} onClick={saveEdit}>Save correction</button>
                <button className="ghost sm" onClick={() => setEditing(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              <div className="mem-head">
                <strong>{m.pinned ? '⚲ ' : ''}{m.title || m.body?.slice(0, 40)}</strong>
                {m.kind && <em className="mem-kind">{m.kind}</em>}
              </div>
              {m.title && <div className="mem-body">{m.body}</div>}
              <div className="mem-meta">
                {m.correctedBy ? 'corrected by you' : m.source === 'user' ? 'added by you' : 'written by the agent'}
                {m.usedCount ? ` · used in ${m.usedCount} runs` : ''}
              </div>
              <div className="mem-actions">
                <button className="ghost sm"
                        onClick={() => setEditing({ memId: m.memId, title: m.title || '', body: m.body || '', kind: m.kind || 'note' })}>
                  <Icon name="edit" size={13} />Correct
                </button>
                <button className="ghost sm" onClick={() => remove(m)}>Forget</button>
              </div>
            </>
          )}
        </div>
      ))}

      <form onSubmit={add} style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 14 }}>
        <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea placeholder={scope === 'shared' ? 'What should every Bot know?' : `What should ${agent.name} remember?`}
                  rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
        <button disabled={!title.trim() || !body.trim()}>
          {scope === 'shared' ? 'Add to shared memory' : 'Add memory'}
        </button>
      </form>
    </>
  );
}

/** The channels this Bot is in: where it works alongside other Bots. */
function Channels({ agent }) {
  const [rooms, setRooms] = useState(null);
  const [agentsById, setAgentsById] = useState({});
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    Promise.all([api.threads(), api.agents()]).then(([t, a]) => {
      if (!live) return;
      setRooms((t.threads || []).filter((x) => x.kind === 'room' && (x.agentIds || []).includes(agent.agentId)));
      setAgentsById(Object.fromEntries((a.agents || []).map((x) => [x.agentId, x])));
    }).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [agent.agentId]);

  if (error) return <div className="err"><span className="msg-text">{error}</span></div>;
  if (!rooms) return <div className="empty">Loading…</div>;
  if (rooms.length === 0) {
    return (
      <div className="empty">
        <span className="title">Not in any channel</span>
        <span>Make a channel from the + in your inbox and add {agent.name} to it.</span>
      </div>
    );
  }
  return (
    <ul className="chan-list">
      {rooms.map((r) => (
        <li key={r.threadId}>
          <Link to={`/rooms/${r.threadId}`} className="chan-row">
            <span className="chan-stack" aria-hidden="true">
              {(r.agentIds || []).slice(0, 3).map((id) => agentsById[id] && (
                <Companion key={id} archetype={agentsById[id].avatar?.shape || 'pebble'}
                           color={agentsById[id].avatar?.color || '#2f6fe4'} state="idle" size={22} />
              ))}
            </span>
            <span className="chan-text">
              <strong>{r.title}</strong>
              <small>{(r.agentIds || []).length} Bots{r.status && r.status !== 'active' ? ` · ${r.status}` : ''}</small>
            </span>
          </Link>
        </li>
      ))}
    </ul>
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

export function Skills({ agent, onChange }) {
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

// Routines first: the question beside a conversation is "what does this one do
// on its own?". Computer stays where it was built and is otherwise untouched --
// per-Bot computers are on hold, and this pass adds nothing to them.
const TABS = ['Routines', 'Channels', 'Memory', 'Skills', 'Access', 'Activity', 'Usage', 'Computer'];

/**
 * The three things a header offers: share the conversation, open the Bot's
 * settings, close the pane. Share is two real actions -- a link that opens this
 * conversation, and the conversation as a Markdown file -- not a button that
 * promises sharing and does nothing.
 */
function PaneActions({ agent, onClose, onExport }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}`);
      setNote('Link copied');
    } catch { setNote('Copy is blocked here; copy the address bar instead'); }
  }

  function download() {
    const blob = new Blob([onExport?.() || ''], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${agent.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-conversation.md`;
    a.click();
    URL.revokeObjectURL(url);
    setNote('Downloaded');
  }

  return (
    <div className="pane-actions">
      <button type="button" className="ghost sm icon-btn" aria-label="Share" aria-haspopup="menu"
              aria-expanded={open} onClick={() => { setOpen((o) => !o); setNote(''); }}>
        <Icon name="share" size={17} />
      </button>
      <Link className="ghost sm icon-btn" to={`/agents/${agent.agentId}/settings`}
            aria-label={`${agent.name} settings`}>
        <Icon name="sliders" size={17} />
      </Link>
      {onClose && (
        <button type="button" className="ghost sm icon-btn pane-close" onClick={onClose} aria-label="Close">
          <Icon name="x" size={17} />
        </button>
      )}
      {open && (
        <>
          <div className="cmp-scrim" onClick={() => setOpen(false)} />
          <div className="pane-menu" role="menu">
            <button type="button" role="menuitem" onClick={copyLink}>
              <Icon name="share" size={16} />Copy link to this conversation
            </button>
            <button type="button" role="menuitem" onClick={download} disabled={!onExport}>
              <Icon name="download" size={16} />Download as Markdown
            </button>
            {note && <p className="pane-menu-note" role="status">{note}</p>}
          </div>
        </>
      )}
    </div>
  );
}

export default function RightPanel({ threadId, agent, agents, onRefreshAgent, open, onClose, onExport }) {
  const [tab, setTab] = useState('Routines');
  const cls = `task-side ${open ? 'open' : ''}`;
  if (!agent) return <aside className={cls} />;

  return (
    <aside className={cls}>
      <div className="task-side-head">
        <strong>{agent.name}</strong>
        <PaneActions agent={agent} onClose={onClose} onExport={onExport} />
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="tabbody">
        {tab === 'Routines' && <RoutineList agent={agent} />}
        {tab === 'Channels' && <Channels agent={agent} />}
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
