import { useState } from 'react';
import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import { api } from '../../api';
import { startLogin, useAuth0 } from '../../auth0';

const TIERS = [
  { key: 'explore', name: 'Explore', price: '$0', period: '/month', credits: '100 Amaz Credits', tagline: 'Build your first AI team', includes: ['Try skills, agents, and safe tasks'] },
  { key: 'personal', name: 'Personal', price: '$19', period: '/month', credits: '1,000 Amaz Credits', tagline: 'Your work, delegated', includes: ['Agent workspace', 'Connected tools', 'Approval-first actions'] },
  { key: 'personal_plus', name: 'Personal+', price: '$39', period: '/month', credits: '2,500 Amaz Credits', tagline: 'More capacity for daily work', includes: ['More agents', 'More routines', 'Add-on credits'], featured: true },
  { key: 'pro', name: 'Pro', price: '$79', period: '/month', credits: '6,000 Amaz Credits', tagline: 'For power users', includes: ['Priority runs', 'Advanced Skills', 'More connected work'] },
  { key: 'power', name: 'Power', price: '$149', period: '/month', credits: '12,000 Amaz Credits', tagline: 'For people who run their work through AmazAI', includes: ['Maximum capacity', 'Advanced controls', 'Priority support'] },
];

const FAQ = [
  { q: 'What is an Amaz Credit?', a: 'One unit of metered agent work — a model call, a tool use, a scheduled routine tick. Read-only checks and approvals themselves never cost credits.' },
  { q: 'What happens if I run out?', a: 'Agents pause new work and tell you what they were about to do. Nothing queues up silently and nothing overspends without your say-so — top up with add-on credits or wait for your next cycle.' },
  { q: 'Do unused credits roll over?', a: 'No — each plan renews with a fresh allotment every billing cycle, which keeps usage predictable for you and for us.' },
  { q: 'Can I add credits without upgrading my plan?', a: 'Yes. Add-on credit packs are available on every paid tier for a month with more going on than usual.' },
  { q: 'What is the difference between a personal plan and For Teams?', a: 'Personal plans are one seat, one workspace. For Teams adds shared workspaces, pooled team credits, admin controls, and audit history across everyone on the team.' },
];

export default function Pricing() {
  const [openFaq, setOpenFaq] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const { isAuthenticated, loginWithRedirect } = useAuth0();

  // Signed in: start checkout for that exact plan and go straight to
  // Stripe. Signed out: the same Universal Login signup every other "get
  // started" button on this site uses -- a brand-new account still has
  // onboarding to get through (meeting Chief) before a plan means anything,
  // so this does not try to carry the chosen tier through that flow. The
  // Billing page (linked from account nav once signed in) is one more click
  // away to pick a plan for real.
  async function getStarted(planKey) {
    if (!isAuthenticated) {
      startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' });
      return;
    }
    setBusy(planKey); setError('');
    try {
      // success/cancel URLs are not sent from here: the server builds them
      // from the request's own Origin header (services/handlers/api.py),
      // never from client input -- a client-supplied redirect target would
      // be an open redirect through Stripe's own domain.
      const { url } = await api.billing.checkout({ planKey });
      window.location.href = url;
    } catch (err) {
      setError(err.message);
      setBusy('');
    }
  }

  return (
    <PublicShell wide>
      <section className="about-hero">
        <h1>Pricing that scales with the work, not the seat.</h1>
        <p>Every plan includes the same approval-first guardrails. What changes is how much work your team can run.</p>
      </section>

      {error && <div className="err"><span className="msg-text">{error}</span></div>}

      <section className="pricing-grid">
        {TIERS.map((t) => (
          <article key={t.name} className={t.featured ? 'pricing-card featured' : 'pricing-card'}>
            {t.featured && <span className="pricing-badge">Most popular</span>}
            <h2>{t.name}</h2>
            <p className="pricing-price"><strong>{t.price}</strong>{t.period}</p>
            <p className="pricing-credits">{t.credits}</p>
            <p className="pricing-tagline">{t.tagline}</p>
            <ul>
              {t.includes.map((i) => <li key={i}>{i}</li>)}
            </ul>
            <button
              className={t.featured ? 'primary' : 'ghost'}
              disabled={busy === t.key}
              onClick={() => getStarted(t.key)}
            >
              {busy === t.key ? 'Redirecting…' : 'Get started'}
            </button>
          </article>
        ))}
      </section>

      <section className="pricing-teams">
        <h2>For Teams</h2>
        <p>
          Startup and SMB plans add shared workspaces, team credits, approval
          controls, audit history, and administration.
        </p>
        <Link to="/for-teams" className="primary" style={{ display: 'inline-block', textDecoration: 'none' }}>
          Talk to us
        </Link>
      </section>

      <section className="about-contact">
        <h2>Credit FAQ</h2>
        <div className="faq-list">
          {FAQ.map((item, i) => (
            <div key={item.q} className="faq-item">
              <button
                type="button"
                className="faq-question"
                onClick={() => setOpenFaq(openFaq === i ? null : i)}
                aria-expanded={openFaq === i}
              >
                {item.q}
                <span aria-hidden="true">{openFaq === i ? '−' : '+'}</span>
              </button>
              {openFaq === i && <p className="faq-answer">{item.a}</p>}
            </div>
          ))}
        </div>
      </section>
    </PublicShell>
  );
}
