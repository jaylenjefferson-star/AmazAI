import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { operatorFirstName, useAuth0 } from '../auth0';
import Companion from '../characters/Companion';
import { ARCHETYPES, ARCHETYPE_KEYS } from '../characters/archetypes';
import Icon from '../components/Icon';
import Problem from '../components/Problem';
import ToolsSheet from '../components/ToolsSheet';
import { COPY } from '../lib/errors';

const TIER = {
  fast: ['Fast', 'Quick answers, light work.'],
  balanced: ['Balanced', 'The right pick for most jobs.'],
  deep: ['Deep', 'Slower, for hard problems.'],
};

const STYLE_HINT = {
  collaborative: 'Works with you, checks in',
  autonomous: 'Gets on with it',
  concise: 'Short and direct',
  thorough: 'Careful and complete',
};

/** A step of the flow: collapsed to a line until it is the one you are on. */
function Step({ n, icon, title, summary, open, onOpen, children }) {
  return (
    <section className={`na-step${open ? ' is-open' : ''}`}>
      <button type="button" className="na-step-head" onClick={onOpen} aria-expanded={open}>
        <span className="na-n">{n}</span>
        <span className="sx-text"><strong>{title}</strong>{!open && summary && <small>{summary}</small>}</span>
        <Icon name={icon} size={19} />
      </button>
      {open && <div className="na-step-body">{children}</div>}
    </section>
  );
}

/**
 * Hire an agent.
 *
 * Not a form: a character, a name, and then a few short steps that open one at a
 * time. Everything offered still comes from `GET /agents/options`, the same
 * constants the API validates against, so this cannot offer a colour or a model the
 * server would refuse. One idempotency key per visit means a double tap or a retry
 * after a bad connection lands on the same agent, never a second one.
 *
 * What is decided *here* is who the agent is. What it may do is decided by the
 * server, per action; the one line under the steps says the part that matters.
 */
export default function NewAgent() {
  const navigate = useNavigate();
  const { user } = useAuth0();
  const [options, setOptions] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState(1);
  const [tools, setToolsOpen] = useState(false);
  const [installed, setInstalled] = useState([]);
  const [skip, setSkip] = useState(() => new Set());
  const [roster, setRoster] = useState([]);
  const [reportsTo, setReportsTo] = useState('');   // '' = the default, which needs nothing said

  const [name, setName] = useState('');
  const [title, setTitle] = useState('');
  const [role, setRole] = useState('');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [shape, setShape] = useState('pebble');
  const [color, setColor] = useState('#7b93ff');
  const [tier, setTier] = useState('balanced');
  const [style, setStyle] = useState('collaborative');
  const [builtin, setBuiltin] = useState([]);
  const nameRef = useRef(null);
  const key = useMemo(() => `create-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`, []);

  const load = () => {
    setLoadError(null);
    api.agentOptions().then((o) => {
      setOptions(o);
      setTier(o.defaultModelTier || 'balanced');
      if (o.colors?.length && !o.colors.includes(color)) setColor(o.colors[5] ?? o.colors[0]);
    }).catch(setLoadError);
    api.connectors().then((r) => setInstalled(r.connectors || [])).catch(() => {});
    api.agents().then((r) => setRoster(r.agents || [])).catch(() => {});
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [tools]);
  useEffect(() => { if (options) nameRef.current?.focus({ preventScroll: true }); }, [options]);

  const chief = roster.find((a) => a.entrypoint);
  const canCreate = name.trim().length >= 2 && role.trim().length >= 2 && !busy;
  const next = (n) => setStep(n);
  const carousel = useRef(null);
  const shown = (options?.colors || []).slice(0, 10);

  async function create() {
    if (!canCreate) return;
    setBusy(true);
    setProblem(null);
    try {
      const body = {
        name: name.trim(), title: title.trim(), role: role.trim(), description: description.trim(),
        systemPrompt: systemPrompt.trim(), modelTier: tier, workingStyle: style,
        avatar: { shape, color },
        // Read once by the API so the agent can say hello by name; stored nowhere.
        operatorName: operatorFirstName(user),
        tools: builtin,
        // No budget here on purpose: the server applies its default limit and hard-stops
        // at it. It is one thing to change later, from the agent's settings, not a
        // question to answer before the agent exists.
      };
      // Left alone, a new Bot reports to Chief, and nothing is stored for that.
      if (reportsTo) body.reportsTo = reportsTo;
      // Connected apps are given by default. Only if some were switched off is the
      // list sent, so leaving them alone keeps the server's default.
      if (skip.size) {
        body.grants = installed.filter((c) => !skip.has(c.connectorId))
          .map((c) => ({ connectorId: c.connectorId, capability: 'admin', allowedTools: ['*'] }));
      }
      const agent = await api.createAgent(body, key);
      navigate(`/agents/${agent.agentId}`, { replace: true });
    } catch (err) {
      setProblem({ error: err, message: COPY.createAgent(name.trim()) });
      setBusy(false);
    }
  }

  const arch = ARCHETYPES[shape];

  return (
    <div className="na" style={{ '--pick': color }}>
      <header className="na-top">
        <button type="button" className="ch-round" aria-label="Close" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/'))}>
          <Icon name="x" size={20} />
        </button>
        <span className="na-title">New agent</span>
        <span className="ch-round ch-round--ghost" />
      </header>

      <div className="na-scroll">
        {loadError && !options && <Problem error={loadError} onRetry={load} />}

        <div className="na-hero">
          <div className="na-stage"><Companion archetype={shape} color={color} state={busy ? 'thinking' : 'idle'} size={128} name={name || 'your agent'} /></div>
          <input ref={nameRef} className="na-name" value={name} maxLength={40} placeholder="Name your agent"
                 aria-label="Name" onChange={(e) => setName(e.target.value)} />
          <p className="na-blurb">{arch.name}. {arch.blurb}</p>

          {/* Tap through the characters: a swipeable row, the chosen one lifted. */}
          <div className="na-carousel" ref={carousel} role="radiogroup" aria-label="Character">
            {ARCHETYPE_KEYS.map((k) => (
              <button key={k} type="button" role="radio" aria-checked={shape === k} aria-label={ARCHETYPES[k].name}
                      className={`na-char${shape === k ? ' on' : ''}`} onClick={() => setShape(k)}>
                <Companion archetype={k} color={color} state="idle" size={42} />
              </button>
            ))}
          </div>
          <div className="na-colors" role="radiogroup" aria-label="Accent colour">
            {shown.map((c) => (
              <button key={c} type="button" role="radio" aria-checked={color === c} aria-label={c}
                      className={`na-dot${color === c ? ' on' : ''}`} style={{ background: c }} onClick={() => setColor(c)} />
            ))}
          </div>
        </div>

        <div className="na-steps">
          <Step n={1} icon="user" title="Identity" summary={[title, role].filter(Boolean).join(' · ') || 'Title, role, description'}
                open={step === 1} onOpen={() => setStep(1)}>
            <label className="pf-field"><span>Title <em>optional</em></span>
              <input value={title} maxLength={24} placeholder="Operations" onChange={(e) => setTitle(e.target.value)} /></label>
            <label className="pf-field"><span>Role</span>
              <input value={role} maxLength={200} placeholder="Sr Director, Head of Ops" onChange={(e) => setRole(e.target.value)} /></label>
            {roster.length > 0 && (
              <label className="pf-field"><span>Reports to</span>
                <select value={reportsTo} onChange={(e) => setReportsTo(e.target.value)}>
                  <option value="">{chief ? `${chief.name} (default)` : 'You (default)'}</option>
                  {roster.filter((a) => !chief || a.agentId !== chief.agentId)
                    .map((a) => <option key={a.agentId} value={a.agentId}>{a.name}</option>)}
                  {chief && <option value="owner">You</option>}
                </select></label>
            )}
            <label className="pf-field"><span>Description <em>optional</em></span>
              <textarea rows={3} value={description} maxLength={2000} placeholder="Head of business operations and strategy"
                        onChange={(e) => setDescription(e.target.value)} /></label>
            <p className="pf-hint">This is how {name.trim() || 'your agent'} will understand itself, in every conversation, room and task.</p>
            <button type="button" className="na-next" onClick={() => next(2)}>Continue</button>
          </Step>

          <Step n={2} icon="spark" title="Brain" summary={`${(TIER[tier] || [tier])[0]} · ${style}`} open={step === 2} onOpen={() => setStep(2)}>
            <div className="pf-tiers" role="radiogroup" aria-label="Model">
              {(options?.modelTiers || []).map((t) => (
                <button key={t.key} type="button" role="radio" aria-checked={tier === t.key}
                        className={tier === t.key ? 'on' : ''} onClick={() => setTier(t.key)}>
                  <strong>{(TIER[t.key] || [t.key])[0]}</strong><small>{(TIER[t.key] || [])[1]}</small>
                </button>
              ))}
            </div>
            <div className="pf-field"><span>Personality</span>
              <div className="na-chips" role="radiogroup" aria-label="Personality">
                {(options?.workingStyles || []).map((w) => (
                  <button key={w} type="button" role="radio" aria-checked={style === w}
                          className={style === w ? 'on' : ''} onClick={() => setStyle(w)}>
                    {w}{STYLE_HINT[w] ? <small>{STYLE_HINT[w]}</small> : null}
                  </button>
                ))}
              </div>
            </div>
            <label className="pf-field"><span>Core instructions <em>optional</em></span>
              <textarea rows={3} value={systemPrompt} maxLength={20000} placeholder="How it should think and work. Left blank, its role is used."
                        onChange={(e) => setSystemPrompt(e.target.value)} /></label>
            <p className="pf-hint">Memory starts empty and builds as you work together. You can correct or forget anything from its profile.</p>
            <button type="button" className="na-next" onClick={() => next(3)}>Continue</button>
          </Step>

          <Step n={3} icon="plug" title="Capabilities"
                summary={installed.length ? `${installed.length - skip.size} app${installed.length - skip.size === 1 ? '' : 's'} · ${builtin.length ? builtin.join(', ') : 'terminal & files'}` : 'No apps connected yet'}
                open={step === 3} onOpen={() => setStep(3)}>
            <div className="pf-field"><span>Connected apps</span>
              {installed.length === 0 && <p className="pf-hint">Nothing connected yet. Connect an app and every agent can use it. You can also do it later, from the agent&apos;s profile.</p>}
              {installed.map((c) => {
                const on = !skip.has(c.connectorId);
                return (
                  <button key={c.connectorId} type="button" className={`na-app${on ? ' on' : ''}`} aria-pressed={on}
                          onClick={() => setSkip((cur) => { const n = new Set(cur); if (on) n.add(c.connectorId); else n.delete(c.connectorId); return n; })}>
                    <span className="app-logo fallback" aria-hidden="true">{(c.name || c.app).charAt(0).toUpperCase()}</span>
                    <span className="sx-text"><strong>{c.name || c.app}</strong><small>{on ? 'Included' : 'Left out'}</small></span>
                    <Icon name={on ? 'check' : 'plus'} size={18} />
                  </button>
                );
              })}
              <button type="button" className="pf-add" onClick={() => setToolsOpen(true)}><Icon name="plus" size={17} />Connect a tool</button>
            </div>
            <div className="pf-field"><span>Built in</span>
              <p className="pf-hint" style={{ margin: 0 }}>A terminal and its own files are always on.</p>
              <div className="na-chips">
                {['browser', 'code_interpreter'].map((t) => (
                  <button key={t} type="button" aria-pressed={builtin.includes(t)} className={builtin.includes(t) ? 'on' : ''}
                          onClick={() => setBuiltin((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))}>
                    {t === 'browser' ? 'Browser' : 'Code'}
                  </button>
                ))}
              </div>
            </div>
            <p className="pf-hint">Skills are assigned from the agent&apos;s profile once it exists.</p>
          </Step>

        </div>

        <p className="na-assure">It asks before it sends, posts, changes or deletes anything.</p>

        {problem && <Problem message={problem.message} error={problem.error} onRetry={create} />}
      </div>

      <footer className="na-foot">
        <button type="button" className="primary na-create" disabled={!canCreate} onClick={create}>
          {busy ? 'Creating…' : name.trim() ? `Create ${name.trim()}` : 'Create agent'}
        </button>
        {!canCreate && !busy && <small>{name.trim().length < 2 ? 'Give it a name' : 'Add a role to continue'}</small>}
      </footer>

      {tools && <ToolsSheet onClose={() => setToolsOpen(false)} />}
    </div>
  );
}
