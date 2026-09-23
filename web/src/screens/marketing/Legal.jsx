import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const DOCS = [
  { to: '/terms', title: 'Terms of Use', body: 'The agreement covering your use of AmazAI.' },
  { to: '/privacy', title: 'Privacy Policy', body: 'What we collect, why, and how it is stored and deleted.' },
  { to: '/cookie-policy', title: 'Cookie Policy', body: 'The cookies AmazAI sets and why.' },
  { to: '/acceptable-use', title: 'Acceptable Use Policy', body: 'What agents may not be used to do.' },
  { to: '/security-disclosure', title: 'Security & Responsible Disclosure', body: 'How we handle security reports, and how to send us one.' },
];

const COMING_SOON = [
  { title: 'Subprocessors', body: 'A public list of the vendors AmazAI relies on to run the service.' },
  { title: 'Data Processing Agreement (DPA)', body: 'For teams that need a signed DPA in place before onboarding.' },
];

export default function Legal() {
  return (
    <PublicShell>
      <Seo
        title="Legal & Trust"
        description="Terms of Use, Privacy Policy, Cookie Policy, Acceptable Use Policy, and our Security & Responsible Disclosure policy, all in one place."
        path="/legal"
      />
      <section className="about-hero">
        <h1>Legal &amp; Trust</h1>
        <p>Every policy that governs how AmazAI is built and run, in one place.</p>
      </section>

      <section className="about-grid">
        {DOCS.map((d) => (
          <Link key={d.to} to={d.to} className="about-card" style={{ textDecoration: 'none', color: 'inherit' }}>
            <h2>{d.title}</h2>
            <p>{d.body}</p>
          </Link>
        ))}
      </section>

      <section className="about-contact">
        <h2>Coming soon</h2>
        <div className="about-grid">
          {COMING_SOON.map((d) => (
            <div key={d.title} className="about-card muted">
              <h2>{d.title}</h2>
              <p>{d.body}</p>
            </div>
          ))}
        </div>
      </section>
    </PublicShell>
  );
}
