import { Link } from 'react-router-dom';
import Companion from '../../characters/Companion';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const FLOW = [
  {
    n: '01', title: 'Create your team',
    body: 'Pick a small cast of agents, each with a role, a budget, and a narrow set of tools. A tool nobody granted is absent from the agent entirely — never present-and-refused.',
    archetype: 'jelly', color: '#12a594',
  },
  {
    n: '02', title: 'Assign work',
    body: 'Give an agent a task in plain language, or put it on a schedule. It plans, uses the tools it was granted, and reports back as it goes.',
    archetype: 'paper', color: '#2b6bff',
  },
  {
    n: '03', title: 'Approve actions',
    body: 'Read-only work happens on its own. Anything irreversible — sending, spending, deleting, granting access — stops and shows you the exact arguments before it runs.',
    archetype: 'lantern', color: '#e8833a',
  },
  {
    n: '04', title: 'Verify outcomes',
    body: 'Every run ends with a result you can check against what was asked, not just a transcript you have to trust.',
    archetype: 'cloud', color: '#8b2fe0',
  },
  {
    n: '05', title: 'Review the record',
    body: 'A sealed, append-only history of what happened, when, and who approved it — survives even if the agent that produced it is deleted later.',
    archetype: 'moth', color: '#e93d82',
  },
];

/**
 * The flow page.
 *
 * Home sells the promise; this page is where a skeptical visitor comes to
 * check whether "you're in control" is a slogan or an actual mechanism. The
 * "why control matters" section is the answer: the five steps above are only
 * safe because of it.
 */
export default function HowItWorks() {
  return (
    <PublicShell wide>
      <Seo
        title="How It Works"
        description="Five steps from creating your agent team to a sealed audit record — see how AmazAI keeps a human in the loop on everything that matters."
        path="/how-it-works"
      />
      <section className="about-hero">
        <h1>Five steps, and a human in the loop on every one that matters.</h1>
        <p>
          AmazAI agents plan and act on their own. What happens around that —
          scoped tools, an approval gate, and a durable record — is what makes
          it safe to hand them real work.
        </p>
      </section>

      <section className="flow-steps">
        {FLOW.map((s) => (
          <article key={s.n} className="flow-step">
            <Companion archetype={s.archetype} color={s.color} state="idle" size={56} />
            <div>
              <span className="step-n">{s.n}</span>
              <h2>{s.title}</h2>
              <p>{s.body}</p>
            </div>
          </article>
        ))}
      </section>

      <section className="about-contact" id="why-control-matters">
        <h2>Why control matters</h2>
        <p>
          Agents that can collaborate and execute are only as trustworthy as
          the boundary around them. AmazAI enforces that boundary in code,
          not in a system prompt asking an agent to behave: grants, budgets,
          rate limits, and approval gates are decided before the model is ever
          invoked. A tool an agent may not use is absent from its schema —
          it can't ask for it, refuse it, or talk its way around it.
        </p>
        <p>
          Approvals are bound to the exact arguments of the action requested,
          and they expire to <strong>denied</strong>, never to a silent grant.
          Every material action — every tool call, handoff, and approval
          decision — lands in an append-only record that isn't rewritten
          later, even if the agent that produced it is deleted. That is the
          actual differentiator here: not that the agents are capable, but
          that the humans stay accountable. Read the mechanics on the{' '}
          <Link to="/security">Security</Link> page.
        </p>
      </section>
    </PublicShell>
  );
}
