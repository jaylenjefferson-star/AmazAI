import Companion from '../characters/Companion';
import Logo from './../components/Logo';
import { PublicFooter } from '../components/PublicShell';
import { Link } from 'react-router-dom';
import { startLogin, useAuth0 } from '../auth0';

const STEPS = [
  {
    n: '01',
    title: 'Choose your companions',
    body: 'A small cast, each with its own shape, role and budget. They are not chat windows; they are seats that persist.',
    archetype: 'jelly', color: '#12a594', state: 'idle',
  },
  {
    n: '02',
    title: 'Connect your tools',
    body: 'Grant a connector to the workspace, then to one companion at a time. A tool nobody granted is absent, not refused.',
    archetype: 'paper', color: '#2b6bff', state: 'thinking',
  },
  {
    n: '03',
    title: 'Run it — with a hand on the brake',
    body: 'Read-only work just happens. Anything irreversible stops and asks you first, with the exact arguments on screen.',
    archetype: 'lantern', color: '#e8833a', state: 'approval',
  },
];

/**
 * The pre-auth screen.
 *
 * It has one job: make it obvious what AmazAI is before anyone is asked to
 * sign in. The companions are the argument — a row of feature bullets would
 * describe the product; watching a lantern hold up an approval token shows
 * the part that actually matters.
 */
export default function Landing() {
  const { loginWithRedirect } = useAuth0();

  return (
    <div className="landing">
      <header className="landing-nav">
        <Logo size={26} title="AmazAI" />
        <nav className="landing-nav-links">
          <Link to="/about">About</Link>
          <Link to="/security">Security</Link>
        </nav>
        <span style={{ flex: 1 }} />
        <button className="ghost" onClick={() => startLogin(loginWithRedirect, { returnTo: '/' })}>
          Sign in
        </button>
        <button className="primary" onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' })}>
          Create your AmazAI
        </button>
      </header>

      <section className="landing-hero">
        <div className="landing-cast" aria-hidden="true">
          <Companion archetype="pebble"  color="#8b2fe0" state="idle"     size={62} />
          <Companion archetype="cloud"   color="#2b6bff" state="working"  size={86} />
          <Companion archetype="moth"    color="#e93d82" state="thinking" size={58} />
        </div>

        <h1>
          A small cast of companions that
          <span className="grad"> think, act, and show their work.</span>
        </h1>
        <p>
          AmazAI is a private operator console. You give a companion a job, a
          budget and a narrow set of tools — it does the work on its own cloud
          computer and stops for you before anything it cannot undo.
        </p>

        <div className="landing-cta">
          <button className="primary lg"
                  onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' })}>
            Create your AmazAI
          </button>
          <button className="lg" onClick={() => startLogin(loginWithRedirect, { returnTo: '/' })}>
            I already have one
          </button>
        </div>
        <p className="landing-fine">
          Sign-in is handled by Auth0. AmazAI never sees your password.
        </p>
      </section>

      <section className="landing-steps">
        {STEPS.map((s) => (
          <article key={s.n} className="landing-step">
            <Companion archetype={s.archetype} color={s.color} state={s.state} size={64} />
            <span className="step-n">{s.n}</span>
            <h2>{s.title}</h2>
            <p>{s.body}</p>
          </article>
        ))}
      </section>

      <PublicFooter />
    </div>
  );
}
