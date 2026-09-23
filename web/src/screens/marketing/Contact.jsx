import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const REASONS = [
  { title: 'Get pricing', body: 'Tell us your team size and what you want agents to do, and we will help pick the right plan.', mailto: 'mailto:hello@amazai.co?subject=Pricing%20question' },
  { title: 'Request a demo', body: 'See AmazAI run a real task end to end, approval and all, on a call with our team.', mailto: 'mailto:hello@amazai.co?subject=Demo%20request' },
  { title: 'Apply for design partner access', body: 'We are working closely with a small number of early teams to shape the roadmap. Tell us what you would automate first.', mailto: 'mailto:hello@amazai.co?subject=Design%20partner%20application' },
];

export default function Contact() {
  return (
    <PublicShell>
      <Seo
        title="Contact / Demo"
        description="Get pricing, request a live demo, or apply for design partner access — email us and a real person will get back to you."
        path="/contact"
      />
      <section className="about-hero">
        <h1>Contact / Demo</h1>
        <p>Whatever brings you here, the fastest way to reach us is email - a real person reads every message.</p>
      </section>

      <section className="about-grid">
        {REASONS.map((r) => (
          <div key={r.title} className="about-card">
            <h2>{r.title}</h2>
            <p>{r.body}</p>
            <a className="ghost" style={{ display: 'inline-block', marginTop: '0.75rem', textDecoration: 'none' }} href={r.mailto}>
              {r.title}
            </a>
          </div>
        ))}
      </section>

      <section className="about-contact">
        <h2>Or just email us</h2>
        <p><a href="mailto:hello@amazai.co">hello@amazai.co</a></p>
      </section>
    </PublicShell>
  );
}
