import { useState } from 'react';
import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';
import { api } from '../../api';
import { startLogin, useAuth0 } from '../../auth0';

// Four plans, not a fragmented ladder. Each answers the same three questions
// so a visitor can self-select: who it is for, how much work it includes, and
// the one capability that changes at this level. Enterprise is a conversation
// rather than a checkout (contact === true), so it renders a link, not a
// Stripe button. Plan keys are the identifiers the billing API expects.
const TIERS = [
  {
    key: 'personal', name: 'Personal', price: '$19', period: '/month',
    who: 'For individuals delegating their own work.',
    usage: '1,000 Amaz Credits each month',
    unlocks: 'One workspace with connected tools and approval-first actions',
  },
  {
    key: 'pro', name: 'Pro', price: '$49', period: '/month', featured: true,
    who: 'For power users who run their day through AmazAI.',
    usage: '5,000 Amaz Credits each month',
    unlocks: 'More companions, more routines, and priority runs',
  },
  {
    key: 'business', name: 'Business', price: '$99', period: '/seat / month',
    who: 'For teams working together in shared rooms.',
    usage: 'Pooled team credits across every seat',
    unlocks: 'Shared workspaces, admin controls, and audit history',
  },
  {
    key: 'enterprise', name: 'Enterprise', price: 'Custom', period: '',
    contact: true,
    who: 'For organizations with security and scale requirements.',
    usage: 'Volume credits with custom limits',
    unlocks: 'SSO, advanced governance, and dedicated support',
  },
];

const FAQ = [
  { q: 'Is there a free way to start?', a: 'Yes. Start free with no credit card — create your first companion and try real tasks before you pick a paid plan.' },
  { q: 'What is an Amaz Credit?', a: 'One unit of metered agent work — a model call, a tool use, a scheduled routine tick. Read-only checks and approvals themselves never cost credits.' },
  { q: 'What happens if I run out?', a: 'Agents pause new work and tell you what they were about to do. Nothing queues up silently and nothing overspends without your say-so — top up with add-on credits or wait for your next cycle.' },
  { q: 'Do unused credits roll over?', a: 'No — each plan renews with a fresh allotment every billing cycle, which keeps usage predictable for you and for us.' },
  { q: 'What is the difference between Personal, Pro, and Business?', a: 'Personal and Pro are single-seat plans; Pro simply includes more capacity and priority. Business adds shared workspaces, pooled team credits, admin controls, and audit history across everyone on the team.' },
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
      <Seo
        title="Pricing"
        description="Four simple plans — Personal, Pro, Business, and Enterprise — all built on the same approval-first guardrails. Start free, no credit card required."
        path="/pricing"
      />
      <section className="about-hero">
        <h1>Simple plans that scale with the work.</h1>
        <p>Every plan runs on the same approval-first guardrails. Start free — no credit card required — and pick a plan when your team is ready.</p>
      </section>

      {error && <div className="err"><span className="msg-text">{error}</span></div>}

      <section className="pricing-grid">
        {TIERS.map((t) => (
          <article key={t.name} className={t.featured ? 'pricing-card featured' : 'pricing-card'}>
            {t.featured && <span className="pricing-badge">Most popular</span>}
            <h2>{t.name}</h2>
            <p className="pricing-price"><strong>{t.price}</strong>{t.period}</p>
            <p className="pricing-tagline">{t.who}</p>
            <ul>
              <li><span className="pricing-q">Usage</span>{t.usage}</li>
              <li><span className="pricing-q">Unlocks</span>{t.unlocks}</li>
            </ul>
            {t.contact ? (
              <Link to="/contact" className="ghost" style={{ display: 'block', textAlign: 'center', textDecoration: 'none' }}>
                Talk to sales
              </Link>
            ) : (
              <button
                className={t.featured ? 'primary' : 'ghost'}
                disabled={busy === t.key}
                onClick={() => getStarted(t.key)}
              >
                {busy === t.key ? 'Redirecting…' : 'Get started'}
              </button>
            )}
          </article>
        ))}
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
