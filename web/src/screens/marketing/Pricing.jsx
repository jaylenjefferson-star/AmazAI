import { useState } from 'react';
import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';
import { api } from '../../api';
import { startLogin, useAuth0 } from '../../auth0';

// These values mirror services/amazai/billing_plans.json, the checkout source
// of truth. A marketing price that differs from the Stripe-backed plan is not
// a harmless typo: it changes the offer a customer thinks they accepted.
// Each card answers the same three questions
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
    key: 'personal_plus', name: 'Personal+', price: '$39', period: '/month', featured: true,
    who: 'For people delegating work throughout the week.',
    usage: '2,500 Amaz Credits each month',
    unlocks: 'More capacity for companions, routines, and connected work',
  },
  {
    key: 'pro', name: 'Pro', price: '$79', period: '/month',
    who: 'For power users who run their day through AmazAI.',
    usage: '6,000 Amaz Credits each month',
    unlocks: 'Priority runs, advanced skills, and more connected work',
  },
  {
    key: 'power', name: 'Power', price: '$149', period: '/month',
    who: 'For people running most of their work through AmazAI.',
    usage: '12,000 Amaz Credits each month',
    unlocks: 'Maximum self-serve capacity, advanced controls, and priority support',
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
  { q: 'Do unused credits roll over?', a: 'Your current purchased balance remains visible in Billing while the account is active. Promotional or trial credits can have separate limits shown when they are granted.' },
  { q: 'How do the self-serve plans differ?', a: 'The plans use the same approval-first foundation. Higher tiers include more monthly credits and unlock additional capacity or priority. Enterprise arrangements can add organization-specific security, governance, retention, and support terms.' },
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
        description="Self-serve and enterprise plans built on the same approval-first guardrails. Start free, no credit card required."
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

      <p className="hint-text" style={{ textAlign: 'center', margin: '1rem auto 3rem', maxWidth: 760 }}>
        Paid plans renew monthly until canceled. By subscribing, you agree to the{' '}
        <Link to="/terms">Terms of Use</Link> and{' '}
        <Link to="/billing-policy">Billing, Cancellation &amp; Refund Policy</Link>.
        See the <Link to="/privacy">Privacy Policy</Link> for how account and billing data are handled.
      </p>

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
