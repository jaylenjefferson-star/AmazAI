import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const PILLARS = [
  {
    title: 'Approval-first actions',
    body: 'Grants, budgets, rate limits, and approval gates are decided in code before the model is ever invoked. A tool an agent may not use is absent from its schema, not present-and-refused. Anything irreversible - sending, spending, deleting, granting access - stops and shows you the exact arguments before it runs, and approvals expire to denied, never to a silent grant.',
  },
  {
    title: 'Scoped access',
    body: 'Every agent runs under its own execution role, scoped to its own storage prefix. There is no shared role that makes one agent a peer of every other on the drive. Connector grants are set per agent, per action - a connector installed for the org grants nothing until it is handed to one agent at a time.',
  },
  {
    title: 'Audit history',
    body: 'Every tool call, handoff, and approval decision lands in an append-only evidence record. A sealed bundle is never rewritten, and it survives deleting the agent that produced it.',
  },
  {
    title: 'Isolation',
    body: 'Each agent gets its own cloud computer - its own session, its own filesystem, its own credentials. Session storage is a cache, not a system of record: anything that matters is synced out before a run ends, including on failure.',
  },
];

export default function SecurityOverview() {
  return (
    <PublicShell wide>
      <Seo
        title="Security"
        description="Approval-first actions, scoped access, an append-only audit history, and per-agent isolation — AmazAI's security model is enforced in code, not a prompt."
        path="/security"
      />
      <section className="about-hero">
        <h1>Security is enforced in code, not asked for in a prompt.</h1>
        <p>
          The mechanism behind every claim on this page is the same one described on{' '}
          <Link to="/how-it-works#why-control-matters">Why control matters</Link>: enforcement
          happens before the model is invoked, not after.
        </p>
      </section>

      <section className="about-grid">
        {PILLARS.map((p) => (
          <div key={p.title} className="about-card">
            <h2>{p.title}</h2>
            <p>{p.body}</p>
          </div>
        ))}
      </section>

      <section className="about-contact">
        <h2>Responsible disclosure</h2>
        <p>
          If you believe you have found a security issue, we want to hear
          from it before anyone else does. Our disclosure policy, including
          how to report and what to expect, is here:
        </p>
        <p>
          <Link to="/security-disclosure">Security & Responsible Disclosure policy</Link>
        </p>
      </section>
    </PublicShell>
  );
}
