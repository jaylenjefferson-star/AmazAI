import { useEffect, useState } from 'react';
import { api } from '../api';

const usd = (n) => `$${Number(n ?? 0).toFixed(2)}`;

const TIER_LABEL = {
  trial: 'Free trial', explore: 'Explore', personal: 'Personal',
  personal_plus: 'Personal+', pro: 'Pro', power: 'Power',
};

const PLAN_ORDER = ['explore', 'personal', 'personal_plus', 'pro', 'power'];

/**
 * The account's own balance, tier, and subscription -- self-service, this
 * account only. Renders inside /usage (labelled "Billing" everywhere it is
 * linked from: AccountMenu, ProfileSheet) rather than as a second, competing
 * page, since the nav already promised that word means this screen.
 *
 * checkout/portal never render a card form: both return a Stripe-hosted URL
 * this redirects the browser to. AmazAI holds a reference to a subscription,
 * never a card number.
 */
export default function BillingSummary() {
  const [billing, setBilling] = useState(null);
  const [plans, setPlans] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    let live = true;
    Promise.all([api.billing.get(), api.billing.plans(), api.billing.ledger(10)])
      .then(([b, p, l]) => {
        if (!live) return;
        setBilling(b); setPlans(p); setLedger(l.entries || []);
      })
      .catch((err) => live && setError(err.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, []);

  async function goTo(promise, key) {
    setBusy(key); setError('');
    try {
      const { url } = await promise;
      window.location.href = url;
    } catch (err) {
      setError(err.message);
      setBusy('');
    }
  }

  if (loading) {
    return <section className="card-list"><p className="hint-text">Loading billing…</p></section>;
  }
  if (error && !billing) {
    return <div className="err"><span className="msg-text">{error}</span></div>;
  }

  const tierLabel = TIER_LABEL[billing.tier] || billing.tier || 'No plan yet';

  return (
    <>
      <section className="usage-hero">
        <div className="usage-total">
          <span className="usage-label">Balance</span>
          <span className="usage-big">{usd(billing.balanceUsd)}</span>
          <span className="usage-sub">
            {billing.creditsRemaining != null
              ? `≈ ${Math.round(billing.creditsRemaining).toLocaleString()} Amaz Credits`
              : 'Real spend, tracked exactly'}
          </span>
        </div>
        <dl className="usage-stats">
          <div><dt>Plan</dt><dd>{tierLabel}</dd></div>
          <div><dt>Subscription</dt>
            <dd>
              <span className={`state-chip ${billing.subscriptionStatus === 'active' ? 'cc-tone-ok'
                : billing.subscriptionStatus ? 'cc-tone-warn' : ''}`}>
                <i className="cc-dot" aria-hidden="true" />
                {billing.subscriptionStatus || 'none'}
              </span>
            </dd>
          </div>
          <div><dt>Out of credits?</dt><dd>{billing.hasCredit ? 'No' : 'Yes — runs are paused'}</dd></div>
        </dl>
      </section>

      {error && <div className="err"><span className="msg-text">{error}</span></div>}

      <section className="card-list">
        <h2 className="section-title">Plans</h2>
        <div className="pricing-grid">
          {PLAN_ORDER.filter((key) => plans?.plans?.[key]).map((key) => {
            const p = plans.plans[key];
            const current = billing.tier === key;
            return (
              <article key={key} className={current ? 'pricing-card featured' : 'pricing-card'}>
                {current && <span className="pricing-badge">Current plan</span>}
                <h2>{p.name}</h2>
                <p className="pricing-price">
                  <strong>${p.priceUsd % 1 === 0 ? p.priceUsd : p.priceUsd.toFixed(2)}</strong>
                  /{p.interval}
                </p>
                <p className="pricing-credits">{p.creditsPerMonth.toLocaleString()} Amaz Credits</p>
                <p className="pricing-tagline">{p.description}</p>
                <button
                  className={current ? 'ghost' : 'primary'}
                  disabled={current || busy === `plan:${key}`}
                  onClick={() => goTo(api.billing.checkout({ planKey: key }), `plan:${key}`)}
                >
                  {current ? 'Current plan' : busy === `plan:${key}` ? 'Redirecting…' : `Switch to ${p.name}`}
                </button>
              </article>
            );
          })}
        </div>
      </section>

      {plans?.creditTopUps?.length > 0 && (
        <section className="card-list">
          <h2 className="section-title">Buy more credits</h2>
          <p className="hint-text">A one-time top-up, on top of whatever plan you're already on.</p>
          <div className="picker">
            {plans.creditTopUps.map((t) => (
              <button
                key={t.lookupKey}
                className="ghost"
                disabled={busy === `topup:${t.lookupKey}`}
                onClick={() => goTo(api.billing.checkout({ topUpKey: t.lookupKey }), `topup:${t.lookupKey}`)}
              >
                {busy === `topup:${t.lookupKey}` ? 'Redirecting…' : `${t.name} — ${usd(t.priceUsd)}`}
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="card-list">
        <h2 className="section-title">Subscription</h2>
        <button
          className="ghost"
          disabled={busy === 'portal'}
          onClick={() => goTo(api.billing.portal(), 'portal')}
        >
          {busy === 'portal' ? 'Redirecting…' : 'Manage subscription'}
        </button>
        <p className="hint-text">
          Update your card, change your plan, or cancel — handled entirely on Stripe's side.
        </p>
      </section>

      {ledger.length > 0 && (
        <section className="card-list">
          <h2 className="section-title">Recent credit activity</h2>
          <div className="run-table" role="table">
            {ledger.map((e) => (
              <div key={e.sk} className="run-row" role="row">
                <span className="run-agent">{e.detail || e.kind}</span>
                <span className={`run-cost num ${e.amountUsd < 0 ? '' : 'positive'}`}>
                  {e.amountUsd < 0 ? '−' : '+'}{usd(Math.abs(e.amountUsd))}
                </span>
                <span className="run-status">{new Date(e.createdAt).toLocaleDateString()}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
