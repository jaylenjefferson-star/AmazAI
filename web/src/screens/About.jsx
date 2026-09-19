import Companion from '../characters/Companion';
import PublicShell from '../components/PublicShell';

/**
 * The "who is behind this" page.
 *
 * Legal documents establish AmazFlow, LLC as the operating company; this
 * page is the plain-language version of the same fact, plus the reasoning a
 * visitor actually wants before they hand an agent a connector.
 */
export default function About() {
  return (
    <PublicShell>
      <section className="about-hero">
        <h1>We build AI companions you can actually trust with a task.</h1>
        <p>
          AmazAI is made by AmazFlow, LLC, a small team based in Georgia. We
          started from a simple frustration: assistants that talk convincingly
          but act invisibly. Everything we build works the other way —
          narrow tools, a visible budget, and a stop before anything
          irreversible.
        </p>
      </section>

      <section className="about-grid">
        <div className="about-card">
          <Companion archetype="jelly" color="#12a594" state="idle" size={52} />
          <h2>What we believe</h2>
          <p>
            An agent should have exactly the tools its job requires, nothing
            an owner didn&rsquo;t grant, and a permanent record of what it
            actually did. Enforcement belongs in code, not in a prompt asking
            it nicely to behave.
          </p>
        </div>
        <div className="about-card">
          <Companion archetype="lantern" color="#e8833a" state="approval" size={52} />
          <h2>How we build it</h2>
          <p>
            Every high-impact action stops for a human decision, with the
            exact arguments on screen. Read-only work happens on its own; the
            things that can&rsquo;t be undone never do.
          </p>
        </div>
        <div className="about-card">
          <Companion archetype="cloud" color="#2b6bff" state="working" size={52} />
          <h2>Where we're headed</h2>
          <p>
            AmazAI is early and improving quickly. We&rsquo;d rather ship a
            smaller product that keeps its promises than a larger one that
            occasionally doesn&rsquo;t.
          </p>
        </div>
      </section>

      <section className="about-contact">
        <h2>Get in touch</h2>
        <p>
          General questions: <a href="mailto:hello@amazai.co">hello@amazai.co</a><br />
          Privacy requests: <a href="mailto:privacy@amazai.co">privacy@amazai.co</a><br />
          Security reports: <a href="mailto:security@amazai.co">security@amazai.co</a>
        </p>
      </section>
    </PublicShell>
  );
}
