import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Companion from '../characters/Companion';
import Logo from '../components/Logo';
import { ARCHETYPES, ARCHETYPE_KEYS } from '../characters/archetypes';
import { useAuth0 } from '../auth0';

// Six of the ten the API accepts (`agents.AVATAR_COLORS`). The blue and the
// purple used to be #2b6bff and #8b2fe0, which are not in that list at all --
// onboarding's first companion would have been refused on submit. Kept as a
// short list rather than the full palette because this is the first screen
// anyone sees, but every entry has to be one the validator allows.
const PALETTE = ['#2f6fe4', '#8b5cf6', '#12a594', '#e8833a', '#e93d82', '#3dc98a'];
const KEY = 'amazai.onboarded';

export function hasOnboarded() {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}
function markOnboarded() {
  try { localStorage.setItem(KEY, '1'); } catch { /* private browsing */ }
}

/**
 * First run.
 *
 * Five steps, and the third one is the point: choosing a shape and a colour
 * is how a workspace stops feeling like someone else's software. The steps
 * before and after it are short so that one does not feel like a chore in a
 * queue.
 */
export default function Onboarding() {
  const nav = useNavigate();
  const { user } = useAuth0();
  const [step, setStep] = useState(0);
  const [workspace, setWorkspace] = useState(
    user?.given_name ? `${user.given_name}'s workspace` : 'My workspace');
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [archetype, setArchetype] = useState('pebble');
  const [color, setColor] = useState(PALETTE[0]);

  const steps = [
    {
      title: 'Welcome to AmazAI',
      body: 'A private place for a small cast of companions that do real work on your behalf. Let us give it a name.',
      state: 'idle',
      content: (
        <label className="field">
          <span>Workspace name</span>
          <input className="big" value={workspace} maxLength={60}
                 onChange={(e) => setWorkspace(e.target.value)} />
        </label>
      ),
      canNext: workspace.trim().length >= 2,
    },
    {
      title: 'Your first companion',
      body: 'Give it a name and a job. You can change both later, and add more whenever you like.',
      state: 'thinking',
      content: (
        <>
          <label className="field">
            <span>Name</span>
            <input className="big" placeholder="Pell" value={name} maxLength={60}
                   onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>What is it for?</span>
            <input placeholder="Watches production and investigates alarms"
                   value={role} maxLength={200}
                   onChange={(e) => setRole(e.target.value)} />
          </label>
        </>
      ),
      canNext: name.trim().length >= 2 && role.trim().length >= 2,
    },
    {
      title: 'Pick its shape',
      body: 'Shape is how you will recognise it at a glance — in a list, a room, a timeline. Colour is the second signal, never the only one.',
      state: 'working',
      content: (
        <>
          <div className="picker">
            {ARCHETYPE_KEYS.map((k) => (
              <button key={k} type="button" title={ARCHETYPES[k].name}
                      aria-label={ARCHETYPES[k].name}
                      className={`swatch lg ${archetype === k ? 'on' : ''}`}
                      onClick={() => setArchetype(k)}>
                <Companion archetype={k} color={color} state="idle" size={38} />
              </button>
            ))}
          </div>
          <p className="hint-text" style={{ textAlign: 'center' }}>
            {ARCHETYPES[archetype].name} — {ARCHETYPES[archetype].blurb}
          </p>
          <div className="picker">
            {PALETTE.map((c) => (
              <button key={c} type="button" aria-label={c}
                      className={`dot-swatch ${color === c ? 'on' : ''}`}
                      style={{ background: c }} onClick={() => setColor(c)} />
            ))}
          </div>
        </>
      ),
      canNext: true,
    },
    {
      title: 'You keep the brake',
      body: 'Read-only work runs on its own. Anything irreversible stops and shows you the exact arguments first — and expires to denied if you never answer.',
      state: 'approval',
      content: (
        <ul className="promise-list">
          <li><strong>Nothing irreversible happens silently.</strong> Approvals name the account, the target and whether it can be undone.</li>
          <li><strong>A tool nobody granted is absent</strong>, not refused. Companions cannot argue their way into access.</li>
          <li><strong>Your credentials stay yours.</strong> Connector tokens live with the provider; AmazAI holds a reference, not a key.</li>
        </ul>
      ),
      canNext: true,
    },
    {
      title: `${name.trim() || 'Your companion'} is ready`,
      body: 'That is the whole setup. Everything else can be changed from Settings.',
      state: 'complete',
      content: null,
      canNext: true,
    },
  ];

  const s = steps[step];
  const last = step === steps.length - 1;

  function finish() {
    markOnboarded();
    nav('/', { replace: true });
  }

  return (
    <div className="onboard">
      <div className="onboard-card">
        <header className="onboard-head">
          <Logo size={24} title="AmazAI" />
          <div className="onboard-dots" aria-label={`Step ${step + 1} of ${steps.length}`}>
            {steps.map((_, i) => (
              <i key={i} className={i <= step ? 'on' : ''} aria-hidden="true" />
            ))}
          </div>
        </header>

        <div className="onboard-stage">
          <Companion archetype={archetype} color={color} state={s.state}
                     size={96} name={name.trim() || 'Your companion'} />
        </div>

        <h1>{s.title}</h1>
        <p>{s.body}</p>

        <div className="onboard-content">{s.content}</div>

        <footer className="onboard-foot">
          {step > 0
            ? <button className="ghost" onClick={() => setStep((n) => n - 1)}>Back</button>
            : <span />}
          <button className="primary" disabled={!s.canNext}
                  onClick={() => (last ? finish() : setStep((n) => n + 1))}>
            {last ? 'Open my workspace' : 'Continue'}
          </button>
        </footer>
      </div>
    </div>
  );
}
