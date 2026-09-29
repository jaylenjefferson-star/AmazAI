import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import Companion from '../characters/Companion';
import Logo from '../components/Logo';
import { ARCHETYPES, ARCHETYPE_KEYS } from '../characters/archetypes';
import { operatorFirstName, useAuth0 } from '../auth0';
import { api } from '../api';
import { rememberSetupDone } from '../hooks/useFirstRun';
import { isRetryableProvisionError } from '../lib/provisionRetry';

// Six of the ten the API accepts (`agents.AVATAR_COLORS`). Kept as a short
// list rather than the full palette because this is the first screen anyone
// sees, but every entry has to be one the validator allows -- a colour the API
// refuses would fail the create at the very last step.
const PALETTE = ['#2f6fe4', '#8b5cf6', '#12a594', '#e8833a', '#e93d82', '#3dc98a'];
const LEGAL_VERSION = '2026.09';

/**
 * First run: meet your first Bot.
 *
 * Four steps, and the second one is the point: choosing a name, a shape and a
 * colour is how a workspace stops feeling like someone else's software. The
 * rest are short so that step does not feel like a chore in a queue.
 *
 * What this creates is the account's **first Bot** -- an agent flagged
 * `entrypoint`, made through the ordinary create path. It is not a tour and
 * not a wizard that ends in an empty inbox: the moment it exists it opens the
 * conversation itself ("What do you mainly want me for?"), which is where the
 * onboarding actually happens. This screen only decides who you meet.
 *
 * Note what it does *not* send: no role, no title, no instructions. Those are
 * the server's to supply for a first Bot (`services/amazai/onboarding.py`) --
 * a prompt shipped in the browser bundle is a prompt anyone can read.
 *
 * The last step must create the Bot or say why it could not. Setup that
 * reports success and leaves the account exactly as empty as it found it is
 * the failure this screen has already had once.
 */
export default function Onboarding() {
  const nav = useNavigate();
  const { user } = useAuth0();
  const [step, setStep] = useState(0);
  const [workspace, setWorkspace] = useState(
    user?.given_name ? `${user.given_name}'s workspace` : 'My workspace');
  const [name, setName] = useState('Chief');
  const [archetype, setArchetype] = useState('pebble');
  const [color, setColor] = useState(PALETTE[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [accepted, setAccepted] = useState(false);
  // True only while automatically retrying a first-time account runtime
  // that is still being created -- see PROVISIONING_RETRY_MAX below.
  const [retrying, setRetrying] = useState(false);
  // One key for the whole run of setup, so a retry after a timeout resolves
  // to the Bot the first attempt created rather than a second one.
  const idempotencyKey = useMemo(
    () => `setup-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
    [],
  );

  const botName = name.trim() || 'Your first Bot';

  const steps = [
    {
      title: 'Welcome to AmazAI',
      body: 'A private place for a small team of Bots that do real work on your behalf. Let us give it a name.',
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
      title: 'Meet your first Bot',
      body: 'It opens the conversation, finds out what you mainly want it for, and takes the first real task. Give it a name and a look; you can change both later.',
      state: 'thinking',
      content: (
        <>
          <label className="field">
            <span>Name</span>
            <input className="big" value={name} maxLength={60}
                   onChange={(e) => setName(e.target.value)} />
          </label>
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
      canNext: name.trim().length >= 2,
    },
    {
      title: 'You keep the brake',
      body: 'Read-only work runs on its own. Anything irreversible stops and shows you the exact arguments first — and expires to denied if you never answer.',
      state: 'approval',
      content: (
        <ul className="promise-list">
          <li><strong>Nothing irreversible happens silently.</strong> Approvals name the account, the target and whether it can be undone.</li>
          <li><strong>A tool nobody granted is absent</strong>, not refused. Bots cannot argue their way into access.</li>
          <li><strong>Your credentials stay yours.</strong> Connector credentials are handled by Composio; AmazAI stores the connection reference it needs to request authorized actions.</li>
        </ul>
      ),
      canNext: true,
    },
    {
      title: `${botName} is ready`,
      body: 'It will say hello as soon as you open your workspace. Everything else can be changed from Settings.',
      state: 'complete',
      content: (
        <label className="field" style={{ alignItems: 'flex-start' }}>
          <span>
            <input type="checkbox" checked={accepted}
                   onChange={(e) => setAccepted(e.target.checked)} />
            {' '}I agree to the <Link to="/terms" target="_blank" rel="noreferrer">Terms of Use</Link>,
            {' '}<Link to="/acceptable-use" target="_blank" rel="noreferrer">Acceptable Use Policy</Link>,
            {' '}and <Link to="/privacy" target="_blank" rel="noreferrer">Privacy Policy</Link>.
          </span>
        </label>
      ),
      canNext: accepted,
    },
  ];

  const s = steps[step];
  const last = step === steps.length - 1;

  // A brand-new owner's account runtime is created lazily, warmed by a
  // best-effort async call the moment they signed in (api._warm_account_harness)
  // -- a head start, not a guarantee. Real AgentCore harness creation is
  // genuinely variable (seconds to a couple of minutes). The server says
  // "retry this request" when it still owns the wait; API Gateway does not,
  // and its 30s kill is "Internal Server Error" / 504. Both are the same
  // wait. Twenty tries at 6s covers that window without hanging a permanent
  // failure (a missing model id, a name conflict) which is not retried.
  // One idempotency key for the whole loop, so a timeout that did create
  // the Bot resolves to it instead of a second one.
  const PROVISIONING_RETRY_MAX = 20;
  const PROVISIONING_RETRY_DELAY_MS = 6000;

  async function finish() {
    if (busy) return;
    setBusy(true);
    setError('');
    for (let attempt = 1; attempt <= PROVISIONING_RETRY_MAX; attempt += 1) {
      try {
        // The Bot first: it is the thing someone would notice missing, and the
        // workspace name is worth nothing without it.
        // eslint-disable-next-line no-await-in-loop
        const bot = await api.createAgent({
          name: name.trim(),
          entrypoint: true,
          // Only so it can say hello by name. Sent, read once, stored nowhere.
          operatorName: operatorFirstName(user),
          avatar: { shape: archetype, color },
        }, idempotencyKey);
        // eslint-disable-next-line no-await-in-loop
        await api.saveSettings({
          workspaceName: workspace.trim(),
          onboarded: true,
          legalAcceptance: { version: LEGAL_VERSION, accepted: true },
        });
        rememberSetupDone();
        // Straight into the conversation: the greeting is waiting there, and an
        // inbox with one unread row would only be a longer way to the same place.
        nav(`/agents/${bot.agentId}`, { replace: true });
        return;
      } catch (err) {
        const stillSettingUp = isRetryableProvisionError(err.message);
        if (stillSettingUp && attempt < PROVISIONING_RETRY_MAX) {
          setRetrying(true);
          // eslint-disable-next-line no-await-in-loop
          await new Promise((r) => { setTimeout(r, PROVISIONING_RETRY_DELAY_MS); });
          continue;
        }
        // Stays on this step with the reason. Navigating anyway would be the
        // original bug with a better story.
        setError(err.message);
        setBusy(false);
        setRetrying(false);
        return;
      }
    }
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
                     size={96} name={botName} />
        </div>

        <h1>{s.title}</h1>
        <p>{s.body}</p>

        <div className="onboard-content">{s.content}</div>

        {retrying && (
          <p className="hint-text" style={{ textAlign: 'center' }}>
            Your workspace is still starting up -- this can take a minute the
            first time. Hang tight, {botName} will be right with you.
          </p>
        )}
        {error && <div className="err"><span className="msg-text">{error}</span></div>}

        <footer className="onboard-foot">
          {step > 0
            ? <button className="ghost" disabled={busy}
                      onClick={() => setStep((n) => n - 1)}>Back</button>
            : <span />}
          <button className="primary" disabled={!s.canNext || busy}
                  onClick={() => (last ? finish() : setStep((n) => n + 1))}>
            {last ? (busy ? (retrying ? 'Almost there…' : 'Setting up…') : `Meet ${botName}`) : 'Continue'}
          </button>
        </footer>
      </div>
    </div>
  );
}
