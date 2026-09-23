import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import Companion from '../characters/Companion';
import { ARCHETYPES, ARCHETYPE_KEYS } from '../characters/archetypes';
import { operatorFirstName, useAuth0 } from '../auth0';

/**
 * Create a bot.
 *
 * Every choice offered here comes from `GET /agents/options`, which serves the
 * same constants the API validates against. The form cannot offer a colour or
 * a tier the server would refuse, because it does not know any others.
 *
 * The grant list is deliberately visible rather than hidden behind a default:
 * an agent with no connectors is the safe starting point and should look like
 * a choice, not an oversight. There is no budget control here -- per-agent
 * spend ceilings were removed (see D7 in docs/architecture/15-open-decisions.md).
 */
export default function CreateAgent({ onClose, onCreated }) {
  const { user } = useAuth0();
  const [options, setOptions] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState('');
  const [title, setTitle] = useState('');
  const [role, setRole] = useState('');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [shape, setShape] = useState('pebble');
  const [color, setColor] = useState('#2f6fe4');
  const [tier, setTier] = useState('balanced');
  const [style, setStyle] = useState('collaborative');
  const [tools, setTools] = useState([]);
  const [grants, setGrants] = useState({});

  const dialogRef = useRef(null);
  const nameRef = useRef(null);
  // One key per dialog, so a double-submit or a retry after a network error
  // lands on the same agent instead of a second one.
  const idempotencyKey = useMemo(
    () => `create-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
    [],
  );

  useEffect(() => {
    api.agentOptions()
      .then((o) => {
        setOptions(o);
        setTier(o.defaultModelTier || 'balanced');
        if (o.colors?.length) setColor(o.colors[5] ?? o.colors[0]);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Focus once the form exists, not once the dialog mounts — at mount there
  // is only a loading line, so focusing then silently does nothing and the
  // first keystroke goes to the page.
  useEffect(() => {
    if (options) nameRef.current?.focus();
  }, [options]);

  function toggleGrant(connectorId, tool) {
    setGrants((g) => {
      const current = new Set(g[connectorId] || []);
      if (current.has(tool)) current.delete(tool); else current.add(tool);
      const next = { ...g };
      if (current.size) next[connectorId] = [...current];
      else delete next[connectorId];
      return next;
    });
  }

  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const agent = await api.createAgent({
        name: name.trim(),
        title: title.trim(),
        role: role.trim(),
        description: description.trim(),
        systemPrompt: systemPrompt.trim(),
        modelTier: tier,
        workingStyle: style,
        avatar: { shape, color },
        // Every new Bot greets. Sent only so it can say hello by name; it is
        // read once by the API and stored nowhere.
        operatorName: operatorFirstName(user),
        tools,
        grants: Object.entries(grants).map(([connectorId, allowedTools]) => ({
          connectorId, allowedTools,
        })),
      }, idempotencyKey);
      onCreated(agent);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const connectors = options?.connectors || [];
  const canSubmit = name.trim().length >= 2 && role.trim().length >= 2 && !busy;

  return (
    <div className="modal-scrim" onMouseDown={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div className="modal" role="dialog" aria-modal="true"
           aria-labelledby="create-agent-title" ref={dialogRef}>
        <header className="modal-head">
          <button className="ghost sm" onClick={onClose} aria-label="Close">✕</button>
          <h2 id="create-agent-title">New agent</h2>
        </header>

        {!options && !error && <div className="empty">Loading options…</div>}

        {options && (
          <form onSubmit={submit}>
            <div className="modal-body">
              <div className="avatar-stage">
                <Companion archetype={shape} color={color} state="idle" size={96}
                           name={name || 'your companion'} />
              </div>

              <input className="big" placeholder="Name your agent" value={name}
                     ref={nameRef} maxLength={60}
                     onChange={(e) => setName(e.target.value)} />

              {/* Archetypes, not abstract shapes: the picker should show the
                  thing you will actually recognise in a list. */}
              <div className="picker" role="radiogroup" aria-label="Character">
                {ARCHETYPE_KEYS.map((k) => (
                  <button key={k} type="button" role="radio" aria-checked={shape === k}
                          aria-label={ARCHETYPES[k].name} title={ARCHETYPES[k].name}
                          className={`swatch lg ${shape === k ? 'on' : ''}`}
                          onClick={() => setShape(k)}>
                    <Companion archetype={k} color={color} state="idle" size={34} />
                  </button>
                ))}
              </div>
              <p className="hint-text" style={{ textAlign: 'center', marginTop: 0 }}>
                {ARCHETYPES[shape].name} — {ARCHETYPES[shape].blurb}
              </p>

              <div className="picker" role="radiogroup" aria-label="Colour">
                {options.colors.map((c) => (
                  <button key={c} type="button" role="radio" aria-checked={color === c}
                          aria-label={c} title={c}
                          className={`dot-swatch ${color === c ? 'on' : ''}`}
                          style={{ background: c }} onClick={() => setColor(c)} />
                ))}
              </div>

              <label className="field">
                <span>Title <em>optional</em></span>
                <input placeholder="Email, Sales, Research…" value={title}
                       maxLength={24} onChange={(e) => setTitle(e.target.value)} />
              </label>

              <label className="field">
                <span>Role</span>
                <input placeholder="What is this agent for?" value={role}
                       maxLength={200} onChange={(e) => setRole(e.target.value)} />
              </label>

              <label className="field">
                <span>Description <em>optional</em></span>
                <textarea rows={2} value={description} maxLength={2000}
                          onChange={(e) => setDescription(e.target.value)} />
              </label>

              <label className="field">
                <span>Instructions <em>optional</em></span>
                <textarea rows={3} value={systemPrompt} maxLength={20000}
                          placeholder="How should it work? Left blank, the role is used."
                          onChange={(e) => setSystemPrompt(e.target.value)} />
              </label>

              <div className="field">
                <span>Model</span>
                <div className="seg">
                  {options.modelTiers.map((t) => (
                    <button key={t.key} type="button"
                            className={tier === t.key ? 'on' : ''}
                            title={`Prefers ${t.ladder[0]}, falling back through ${t.ladder.slice(1).join(', ')}`}
                            onClick={() => setTier(t.key)}>
                      {t.key}
                    </button>
                  ))}
                </div>
                <p className="hint-text">
                  Resolved against what this account actually offers, in order.
                  Nothing is pinned to a model that may not exist here.
                </p>
              </div>

              <div className="field">
                <span>Working style</span>
                <div className="seg">
                  {options.workingStyles.map((w) => (
                    <button key={w} type="button" className={style === w ? 'on' : ''}
                            onClick={() => setStyle(w)}>{w}</button>
                  ))}
                </div>
              </div>

              <div className="field">
                <span>Built-in tools</span>
                <div className="checks">
                  {['browser', 'code_interpreter'].map((t) => (
                    <label key={t} className="check">
                      <input type="checkbox" checked={tools.includes(t)}
                             onChange={() => setTools((ts) => ts.includes(t)
                               ? ts.filter((x) => x !== t) : [...ts, t])} />
                      <span>{t}</span>
                    </label>
                  ))}
                </div>
                <p className="hint-text">
                  A shell and the agent&rsquo;s own drive are always on. Anything
                  riskier is a grant, below.
                </p>
              </div>

              <div className="field">
                <span>Connectors</span>
                {connectors.length === 0 ? (
                  <p className="hint-text">
                    None installed for this organization yet, so this agent starts
                    with no outside access. That is the safe default; grants can be
                    added once a connector is installed.
                  </p>
                ) : connectors.map((c) => (
                  <div key={c.connectorId} className="connector">
                    <div className="connector-head">
                      <strong>{c.connectorId}</strong>
                      <span className="cap">{c.capability}</span>
                    </div>
                    <div className="checks">
                      {c.allowedTools.map((t) => (
                        <label key={t} className="check">
                          <input type="checkbox"
                                 checked={(grants[c.connectorId] || []).includes(t)}
                                 onChange={() => toggleGrant(c.connectorId, t)} />
                          <span>{t}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {error && (
              <div className="err" style={{ margin: '0 20px 12px' }}>
                <span className="msg-text">{error}</span>
              </div>
            )}

            <footer className="modal-foot">
              <button type="button" className="ghost" onClick={onClose}>Cancel</button>
              <button className="primary" disabled={!canSubmit}>
                {busy ? 'Creating…' : 'Create'}
              </button>
            </footer>
          </form>
        )}

        {error && !options && (
          <div className="err" style={{ margin: 20 }}>
            <span className="msg-text">{error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
