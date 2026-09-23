import Companion from '../../characters/Companion';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const AGENT_TRAITS = [
  { title: 'Profiles', body: 'A name, a role, a system prompt, an accent colour, and a model. An agent is a persistent identity, not a chat thread you start over.' },
  { title: 'Roles & boundaries', body: 'Each agent gets only the connectors and actions its role needs. Grants are set per agent, not inherited from a shared pool.' },
  { title: 'Creation', body: 'Stand up a new agent from a template or from scratch in minutes: pick a role, a budget, and the tools it should have.' },
  { title: 'Collaboration', body: 'Agents can hand a task to one another mid-run when the work crosses roles, with the handoff itself recorded in the timeline.' },
];

const SKILL_TRAITS = [
  { title: 'Reusable playbooks', body: 'A Skill packages a proven sequence of steps for a recurring job — onboarding a hire, triaging a support queue, drafting a weekly update — so an agent doesn’t reinvent it each time.' },
  { title: 'How they get approved', body: 'A Skill is reviewed once, the same way an agent’s tool grants are: what it touches, what it can’t undo, and where it must stop and ask.' },
  { title: 'Why they’re reliable', body: 'Because the risky steps were already vetted, running a Skill is more predictable than asking an agent to improvise the same task from a blank prompt.' },
];

/**
 * "Product": Agents and Skills combined onto one page.
 *
 * They ship together on purpose — an agent without Skills is just a chat
 * window with tools, and a Skill without an agent to run it is a document.
 * The anchors (#agents, #skills) exist so the footer and other pages can
 * deep-link into either half.
 */
export default function Product() {
  return (
    <PublicShell wide>
      <Seo
        title="Product: Agents & Skills"
        description="Agents are custom identities with a role, a budget, and scoped tools. Skills are reviewed playbooks that make them reliable. See how they work together."
        path="/product"
      />
      <section className="about-hero">
        <h1>A small cast of agents, and the playbooks that make them reliable.</h1>
        <p>
          Agents are who does the work. Skills are how they do it the same
          careful way every time.
        </p>
      </section>

      <section id="agents" className="product-section">
        <div className="product-section-head">
          <Companion archetype="pebble" color="#8b2fe0" state="idle" size={56} />
          <div>
            <h2>Agents</h2>
            <p>Custom identities with a role, a budget, and a narrow set of tools.</p>
          </div>
        </div>
        <div className="about-grid">
          {AGENT_TRAITS.map((t) => (
            <div key={t.title} className="about-card">
              <h2>{t.title}</h2>
              <p>{t.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="skills" className="product-section">
        <div className="product-section-head">
          <Companion archetype="paper" color="#2b6bff" state="thinking" size={56} />
          <div>
            <h2>Skills</h2>
            <p>Reviewed, reusable playbooks an agent runs instead of improvising.</p>
          </div>
        </div>
        <div className="about-grid">
          {SKILL_TRAITS.map((t) => (
            <div key={t.title} className="about-card">
              <h2>{t.title}</h2>
              <p>{t.body}</p>
            </div>
          ))}
        </div>
      </section>
    </PublicShell>
  );
}
