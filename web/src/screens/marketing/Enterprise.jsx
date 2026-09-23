import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const FEATURES = [
  { title: 'SSO', body: 'Single sign-on for your whole organization, so agent access follows the same identity provider as everything else you run.' },
  { title: 'Compliance & security review', body: 'We will work through your security questionnaire and, where needed, a live review of the enforcement model described on the Security page.' },
  { title: 'Implementation', body: 'A guided rollout: connector setup, agent role design, and an approval workflow that matches how your team actually signs off on work today.' },
  { title: 'Retention', body: 'Custom evidence and session retention windows to match your internal policy, beyond the defaults on individual and team plans.' },
  { title: 'Support', body: 'A named point of contact and a response-time commitment, not a ticket queue.' },
];

export default function Enterprise() {
  return (
    <PublicShell wide>
      <Seo
        title="Enterprise"
        description="SSO, a formal security review, custom retention, guided implementation, and a named point of contact — AmazAI for organizations with enterprise needs."
        path="/enterprise"
      />
      <section className="about-hero">
        <h1>Enterprise</h1>
        <p>For organizations that need SSO, a formal security review, and a retention policy that matches their own.</p>
      </section>

      <section className="about-grid">
        {FEATURES.map((f) => (
          <div key={f.title} className="about-card">
            <h2>{f.title}</h2>
            <p>{f.body}</p>
          </div>
        ))}
      </section>

      <section className="about-contact">
        <h2>Contact sales</h2>
        <p>Enterprise plans are set up directly with our team.</p>
        <Link to="/contact" className="primary" style={{ display: 'inline-block', textDecoration: 'none' }}>
          Contact / Demo
        </Link>
      </section>
    </PublicShell>
  );
}
