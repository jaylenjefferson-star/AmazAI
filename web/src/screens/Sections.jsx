import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import Companion, { STATES } from '../characters/Companion';
import { ARCHETYPES } from '../characters/archetypes';
import { fixtureAgents, fixtureArtifacts, fixtureRoutines } from '../fixtures';
import { useAgents } from '../hooks/useAgents';
import { api } from '../api';
import CreateAgent from '../components/CreateAgent';

function Page({ title, sub, children, action }) {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          <p>{sub}</p>
        </div>
        {action}
      </header>
      {children}
    </div>
  );
}

export function Agents() {
  const { agents, loading, error, reload } = useAgents();
  const location = useLocation();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(location.pathname === '/agents/new');
  const close = () => { setCreating(false); navigate('/agents'); };
  return (
    <Page title="Agents" sub="Your cast. Each one has its own drive, budget and grants."
          action={<button className="btn-link primary" onClick={() => setCreating(true)}>New companion</button>}>
      {creating && <CreateAgent onClose={close} onCreated={() => { close(); reload(); }} />}
      <div className="row-list">
        {loading && <div className="empty">Loading your companions…</div>}
        {error && <div className="empty"><strong>Control plane unavailable</strong><span>{error}</span></div>}
        {!loading && !error && agents.length === 0 && (
          <div className="empty"><strong>Your cast is empty</strong><span>Create a companion when you are ready. Agents only appear here after the control plane creates them.</span></div>
        )}
        {agents.map((a) => (
          <Link key={a.agentId} to={`/agents/${a.agentId}`} className="row-card">
            <Companion archetype={a.archetype} color={a.color} state={a.state}
                       size={46} name={a.name} />
            <div className="row-body">
              <strong>{a.name}</strong>
              <span>{a.role} · {ARCHETYPES[a.archetype].name}</span>
            </div>
            <span className={`state-chip cc-tone-${STATES[a.state].tone}`}>
              <i className="cc-dot" aria-hidden="true" />
              {STATES[a.state].label}
            </span>
          </Link>
        ))}
      </div>
    </Page>
  );
}

function timeAgo(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.round(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export function Rooms() {
  const { agents } = useAgents();
  const [rooms, setRooms] = useState([]);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const byId = Object.fromEntries(agents.map((a) => [a.agentId, a]));

  function reload() {
    api.threads().then((r) => setRooms((r.threads || []).filter((t) => t.kind === 'room')))
      .catch((e) => setError(e.message));
  }
  useEffect(reload, []);

  async function createRoom(e) {
    e.preventDefault();
    if (!title.trim() || picked.length === 0) return;
    setBusy(true);
    try {
      const room = await api.createThread({ kind: 'room', title: title.trim(), agentIds: picked });
      navigate(`/rooms/${room.threadId}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page title="Rooms" sub="Task-bound threads with several companions. Handoffs between them show as read-only activity, not chat you can steer here."
          action={<button className="btn-link primary" onClick={() => setCreating((v) => !v)}>New room</button>}>
      {creating && (
        <form className="row-card" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }} onSubmit={createRoom}>
          <input placeholder="What is this room for?" value={title}
                 onChange={(e) => setTitle(e.target.value)} />
          <div className="picker-grid">
            {agents.map((a) => (
              <label key={a.agentId} className={`pick-chip ${picked.includes(a.agentId) ? 'on' : ''}`}>
                <input type="checkbox" checked={picked.includes(a.agentId)}
                       onChange={(e) => setPicked((p) => (e.target.checked
                         ? [...p, a.agentId] : p.filter((id) => id !== a.agentId)))} />
                {a.name}
              </label>
            ))}
          </div>
          <button className="primary" disabled={busy || !title.trim() || picked.length === 0}>
            Create room
          </button>
        </form>
      )}
      {error && <div className="empty"><strong>Rooms unavailable</strong><span>{error}</span></div>}
      {!error && rooms.length === 0 && (
        <div className="empty"><strong>No rooms yet</strong><span>Create one to coordinate several companions on the same task.</span></div>
      )}
      <div className="row-list">
        {rooms.map((r) => (
          <Link key={r.threadId} to={`/rooms/${r.threadId}`} className="row-card">
            <div className="participants">
              {(r.agentIds || []).map((m) => (
                <Companion key={m} archetype={byId[m]?.archetype} color={byId[m]?.color}
                           state={byId[m]?.state || 'idle'} size={30} name={byId[m]?.name} />
              ))}
            </div>
            <div className="row-body">
              <strong>{r.title}</strong>
              <span>{(r.agentIds || []).map((m) => byId[m]?.name || m).join(', ')} · last activity {timeAgo(r.lastActivity)}</span>
            </div>
            <span className={`state-chip cc-tone-${r.status === 'active' ? 'ok' : 'neutral'}`}>
              <i className="cc-dot" aria-hidden="true" />
              {r.status || 'active'}
            </span>
          </Link>
        ))}
      </div>
    </Page>
  );
}

export function Routines() {
  const routines = fixtureRoutines();
  const byId = Object.fromEntries(fixtureAgents().map((a) => [a.agentId, a]));
  return (
    <Page title="Routines" sub="Work that runs on a schedule, whether or not you are here.">
      <div className="row-list">
        {routines.map((r) => {
          const a = byId[r.agent];
          return (
            <article key={r.id} className="row-card">
              <Companion archetype={a?.archetype} color={a?.color} state="idle"
                         size={38} name={a?.name} />
              <div className="row-body">
                <strong>{r.name}</strong>
                <span>{r.cadence} · next {r.next}</span>
              </div>
            </article>
          );
        })}
      </div>
    </Page>
  );
}

export function Artifacts() {
  const artifacts = fixtureArtifacts();
  return (
    <Page title="Artifacts" sub="What runs produced. Sealed bundles are never rewritten.">
      <div className="row-list">
        {artifacts.map((f) => (
          <article key={f.id} className="row-card">
            <span className="artifact-glyph" aria-hidden="true">▤</span>
            <div className="row-body">
              <strong>{f.name}</strong>
              <span>{f.kind} · {f.agent} · {f.at}</span>
            </div>
            {f.sealed && <span className="state-chip cc-tone-ok">
              <i className="cc-dot" aria-hidden="true" />Sealed</span>}
          </article>
        ))}
      </div>
    </Page>
  );
}
