import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useMemo, useState } from 'react';
import Companion, { STATES } from '../characters/Companion';
import { ARCHETYPES } from '../characters/archetypes';
import { useAgents } from '../hooks/useAgents';
import { api } from '../api';
import { SCHEDULE_PRESETS, describeSchedule } from '../schedules';
import CreateAgent from '../components/CreateAgent';
import Icon from '../components/Icon';

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
  const { agents } = useAgents();
  const location = useLocation();
  const navigate = useNavigate();
  const byId = useMemo(() => Object.fromEntries(agents.map((a) => [a.agentId, a])), [agents]);

  const [routines, setRoutines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [busyId, setBusyId] = useState('');

  const [creating, setCreating] = useState(location.pathname === '/routines/new');
  // Filled in by a routine card in a conversation (`components/Cards.jsx`). A
  // proposal can name a preset, never an expression: it opens this form and the
  // operator still reads it and presses Create.
  const prefill = location.state?.prefill || {};
  const [name, setName] = useState(prefill.name || '');
  const [agentId, setAgentId] = useState(prefill.agentId || '');
  const [prompt, setPrompt] = useState(prefill.prompt || '');
  const [manual, setManual] = useState(false);
  const [presetKey, setPresetKey] = useState(
    SCHEDULE_PRESETS.some((p) => p.key === prefill.preset && p.key !== 'custom')
      ? prefill.preset : 'weekday-9');
  const [customExpression, setCustomExpression] = useState('');
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState('');

  function reload() {
    setLoading(true);
    api.routines()
      .then((r) => {
        setRoutines((r.routines || []).slice()
          .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt))));
        setListError('');
      })
      .catch((e) => setListError(e.message))
      .finally(() => setLoading(false));
  }
  useEffect(reload, []);

  useEffect(() => {
    // Defaulted only when there is exactly one right answer. Guessing with
    // two or more companions on the account would silently assign a
    // routine to whichever happened to load first.
    if (!agentId && agents.length === 1) setAgentId(agents[0].agentId);
  }, [agents, agentId]);

  function close() {
    setCreating(false);
    setName(''); setAgentId(''); setPrompt(''); setManual(false);
    setPresetKey('weekday-9'); setCustomExpression(''); setFormError('');
    if (location.pathname === '/routines/new') navigate('/routines', { replace: true });
  }

  const expression = presetKey === 'custom' ? customExpression.trim()
    : (SCHEDULE_PRESETS.find((p) => p.key === presetKey)?.expression || '');
  const canSubmit = !busy && name.trim().length >= 2 && Boolean(agentId)
    && prompt.trim().length >= 2 && (manual || expression.length > 0);

  async function createRoutine(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setFormError('');
    try {
      await api.createRoutine({
        name: name.trim(),
        agentId,
        prompt: prompt.trim(),
        trigger: manual ? { type: 'manual' } : { type: 'schedule', expression },
        enabled: true,
      });
      close();
      reload();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(routine) {
    setBusyId(routine.routineId);
    try {
      const updated = await api.updateRoutine(routine.routineId, { enabled: !routine.enabled });
      setRoutines((rs) => rs.map((r) => (r.routineId === updated.routineId ? updated : r)));
    } catch (err) {
      setListError(err.message);
    } finally {
      setBusyId('');
    }
  }

  async function archive(routine) {
    // Disabled and unscheduled, never deleted -- the same reason a companion
    // is archived rather than removed: a routine that has already fired owns
    // runs, and their evidence keeps pointing at it.
    if (!window.confirm(
      `Archive "${routine.name}"? It stops firing. Runs it already started, `
      + 'and their sealed evidence, remain.')) return;
    setBusyId(routine.routineId);
    try {
      const archived = await api.archiveRoutine(routine.routineId);
      setRoutines((rs) => rs.map((r) => (r.routineId === archived.routineId ? archived : r)));
    } catch (err) {
      setListError(err.message);
    } finally {
      setBusyId('');
    }
  }

  const visible = routines.filter((r) => r.status !== 'archived');

  return (
    <Page title="Routines" sub="Work that runs on a schedule, whether or not you are here."
          action={<button className="btn-link primary" disabled={!agents.length}
                           onClick={() => setCreating(true)}>New routine</button>}>
      {creating && (
        <form className="row-card"
              style={{ flexDirection: 'column', alignItems: 'stretch', gap: 4 }}
              onSubmit={createRoutine}>
          <label className="field">
            <span>Name</span>
            <input value={name} maxLength={80} autoFocus
                   placeholder="Morning brief" onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Which companion runs it?</span>
            <select value={agentId}
                    onChange={(e) => setAgentId(e.target.value)}>
              <option value="" disabled>Choose a companion</option>
              {agents.map((a) => (
                <option key={a.agentId} value={a.agentId}>{a.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>What should it do?</span>
            <textarea rows={3} value={prompt} maxLength={8000}
                      placeholder="Draft the day from the threads and runs since yesterday."
                      onChange={(e) => setPrompt(e.target.value)} />
          </label>
          <label className="field">
            <span>When</span>
            <div className="seg">
              <button type="button" className={!manual ? 'on' : ''}
                      onClick={() => setManual(false)}>On a schedule</button>
              <button type="button" className={manual ? 'on' : ''}
                      onClick={() => setManual(true)}>Manual only</button>
            </div>
          </label>
          {!manual && (
            <label className="field">
              <span>Cadence</span>
              <select value={presetKey}
                      onChange={(e) => setPresetKey(e.target.value)}>
                {SCHEDULE_PRESETS.map((p) => (
                  <option key={p.key} value={p.key}>{p.label}</option>
                ))}
              </select>
            </label>
          )}
          {!manual && presetKey === 'custom' && (
            <label className="field">
              <span>Expression</span>
              <input value={customExpression}
                     placeholder="cron(0 9 ? * MON-FRI *) or rate(15 minutes)"
                     onChange={(e) => setCustomExpression(e.target.value)} />
            </label>
          )}
          {formError && <div className="err"><span className="msg-text">{formError}</span></div>}
          <div className="sheet-actions">
            <button type="button" className="ghost" onClick={close}>Cancel</button>
            <button className="primary" disabled={!canSubmit}>
              {busy ? 'Creating…' : 'Create routine'}
            </button>
          </div>
        </form>
      )}

      {loading && <div className="empty">Loading your routines…</div>}
      {listError && (
        <div className="empty"><strong>Routines unavailable</strong><span>{listError}</span></div>
      )}
      {!loading && !listError && visible.length === 0 && (
        <div className="empty">
          <strong>No routines yet</strong>
          <span>Give a companion work that happens on its own — a morning brief, an overnight sweep.</span>
        </div>
      )}
      <div className="row-list">
        {visible.map((r) => {
          const a = byId[r.agentId];
          return (
            <article key={r.routineId} className="row-card">
              <Companion archetype={a?.archetype} color={a?.color} state="idle"
                         size={38} name={a?.name} />
              <div className="row-body">
                <strong>{r.name}</strong>
                <span>
                  {a?.name || r.agentId} · {describeSchedule(r.trigger)}
                  {r.lastRun ? ` · last ran ${timeAgo(r.lastRun)}` : ''}
                </span>
              </div>
              <label className="native-toggle" title={r.enabled ? 'Pause' : 'Resume'}>
                <input type="checkbox" checked={r.enabled} disabled={busyId === r.routineId}
                       onChange={() => toggle(r)} />
                <span aria-hidden="true" />
                <span className="sr-only">{r.enabled ? 'Pause' : 'Resume'} {r.name}</span>
              </label>
              <button type="button" className="ghost sm" disabled={busyId === r.routineId}
                      onClick={() => archive(r)}>Archive</button>
            </article>
          );
        })}
      </div>
    </Page>
  );
}

function outcomeTone(outcome) {
  if (outcome === 'completed') return 'ok';
  if (outcome === 'failed' || outcome === 'cancelled' || outcome === 'expired') return 'danger';
  return 'neutral';
}

export function Artifacts() {
  const { agents } = useAgents();
  const byId = useMemo(() => Object.fromEntries(agents.map((a) => [a.agentId, a])), [agents]);
  const [artifacts, setArtifacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    api.artifacts()
      .then((r) => { setArtifacts(r.artifacts || []); setError(''); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Page title="Artifacts" sub="What runs produced. Sealed bundles are never rewritten.">
      {loading && <div className="empty">Loading what your companions produced…</div>}
      {error && <div className="empty"><strong>Artifacts unavailable</strong><span>{error}</span></div>}
      {!loading && !error && artifacts.length === 0 && (
        <div className="empty">
          <strong>Nothing sealed yet</strong>
          <span>A run that finishes seals an evidence bundle here — a manifest of what it did, never rewritten once sealed.</span>
        </div>
      )}
      <div className="row-list">
        {artifacts.map((f) => {
          const a = byId[f.agentId];
          return (
            <article key={f.runId} className="row-card">
              <span className="artifact-glyph" aria-hidden="true">
                <Icon name="layers" size={17} />
              </span>
              <div className="row-body">
                <strong>{f.goal || f.runId}</strong>
                <span>
                  {a?.name || f.agentId} · {timeAgo(f.endedAt || f.startedAt)}
                  {typeof f.costUsd === 'number' ? ` · $${f.costUsd.toFixed(3)}` : ''}
                </span>
                {f.summary && <span>{f.summary}</span>}
              </div>
              <span className={`state-chip cc-tone-${outcomeTone(f.outcome)}`}>
                <i className="cc-dot" aria-hidden="true" />
                {f.outcome || 'unknown'}
              </span>
            </article>
          );
        })}
      </div>
    </Page>
  );
}
