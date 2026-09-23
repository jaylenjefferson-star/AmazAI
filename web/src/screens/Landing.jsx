import Companion from '../characters/Companion';
import { Link } from 'react-router-dom';
import { TopNav, PublicFooter } from '../components/PublicShell';
import Seo from '../components/Seo';
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
      <Seo
        title="Your AI Team, With You in Control"
        description="AmazAI is a private agent operator console. Build a small team of AI agents, hand them real tools, and approve anything they can't undo before it happens."
        path="/welcome-to-amazai"
      />
      <TopNav />

      <section className="landing-hero">
        <div className="landing-cast" aria-hidden="true">
          <Companion archetype="pebble"  color="#8b2fe0" state="idle"     size={62} />
          <Companion archetype="cloud"   color="#2b6bff" state="working"  size={86} />
          <Companion archetype="moth"    color="#e93d82" state="thinking" size={58} />
        </div>

        <h1>
          A customizable AI team that gets
          <span className="grad"> work done — with you in control.</span>
        </h1>
        <p>
          AmazAI is a private operator console. Build a small team of agents,
          assign them real work with real tools, and approve anything they
          can't undo before it happens. Every action is scoped, every run is
          recorded, and nothing moves without a decision you made.
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
          Sign-in is handled by Auth0. AmazAI never sees your password. See
          how it all fits together on the <Link to="/how-it-works">How it works</Link> page.
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

      <section className="landing-control">
        <Companion archetype="lantern" color="#e8833a" state="approval" size={56} />
        <div>
          <h2>Why control matters</h2>
          <p>
            Agents can collaborate and execute — draft, send, ship, spend.
            What keeps a human accountable is what happens around that: scoped
            permissions, an approval gate on anything irreversible, a durable
            memory of what was decided, and an audit trail nobody can quietly
            edit. That combination, not the agents themselves, is what makes
            AmazAI safe to hand real work to. Read more on
            {' '}<Link to="/how-it-works">How it works</Link> and
            {' '}<Link to="/security">Security</Link>.
          </p>
        </div>
      </section>

      <PublicFooter />
    </div>
  );
}
