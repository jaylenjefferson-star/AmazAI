import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';
import Companion from '../../characters/Companion';

const CASES = [
  {
    title: 'Founder operations', archetype: 'jelly', color: '#12a594',
    body: 'A founder-ops agent triages inbox and calendar, drafts investor updates, and tracks follow-ups — posting or sending only after you approve the exact message.',
  },
  {
    title: 'Recruiting & onboarding', archetype: 'cloud', color: '#2b6bff',
    body: 'Screen resumes against a rubric, schedule interviews, and prep a new hire’s accounts and access — with every account grant logged and reversible.',
  },
  {
    title: 'Customer operations', archetype: 'moth', color: '#e93d82',
    body: 'Triage a support queue, draft replies from your tone and docs, and escalate anything ambiguous — nothing goes to a customer without a human reading it first.',
  },
  {
    title: 'Research', archetype: 'lantern', color: '#e8833a',
    body: 'Pull together market or competitive research from the sources you grant, with citations you can check, not a summary you have to trust.',
  },
  {
    title: 'Engineering', archetype: 'paper', color: '#8b2fe0',
    body: 'Triage issues, draft pull requests, and summarize CI failures — merges and deploys stay behind an approval, always.',
  },
];

export default function UseCases() {
  return (
    <PublicShell wide>
      <Seo
        title="Use Cases"
        description="From founder operations to engineering and customer support, see how AmazAI agents handle recurring work with an approval on anything irreversible."
        path="/use-cases"
      />
      <section className="about-hero">
        <h1>Built for the work that repeats.</h1>
        <p>The same guardrails apply everywhere: scoped tools, approval on anything irreversible, a record of what happened.</p>
      </section>

      <section className="usecase-list">
        {CASES.map((c) => (
          <article key={c.title} className="usecase-item">
            <Companion archetype={c.archetype} color={c.color} state="idle" size={52} />
            <div>
              <h2>{c.title}</h2>
              <p>{c.body}</p>
            </div>
          </article>
        ))}
      </section>
    </PublicShell>
  );
}
