import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import PlanCards from './PlanCards';
import { friendly } from '../lib/errors';
import { TIER_LABEL, followBillingUrl, listedTopUps, orderedPlans } from '../lib/billing';

const usd = (n) => `$${Number(n ?? 0).toFixed(2)}`;

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
function checkoutNotice() {
  if (typeof window === 'undefined') return '';
  const checkout = new URLSearchParams(window.location.search).get('checkout');
  if (checkout === 'success') return 'Stripe sent you back. The plan updates when the payment is confirmed.';
  if (checkout === 'cancelled') return 'Checkout was cancelled. No charge was made.';
  return '';
}

export default function BillingSummary() {
  const navigate = useNavigate();
  const [billing, setBilling] = useState(null);
  const [plans, setPlans] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [notice] = useState(checkoutNotice);

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
      followBillingUrl(url, navigate);
    } catch (err) {
      setError(friendly(err, "Couldn't open billing."));
      setBusy('');
    }
  }

  async function confirmExplore() {
    setBusy('plan:explore'); setError('');
    try {
      setBilling(await api.billing.confirmExplore());
    } catch (err) {
      setError(friendly(err, "Couldn't select Explore."));
    } finally {
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

      {notice && <p className="hint-text">{notice}</p>}
      {error && <div className="err"><span className="msg-text">{error}</span></div>}

      <section className="card-list">
        <h2 className="section-title">Plans</h2>
        <PlanCards
          plans={orderedPlans(plans)}
          currentTier={billing.tier}
          busy={busy}
          subscribed={!!billing.hasStripeCustomer}
          onExplore={confirmExplore}
          onCheckout={(planKey) => goTo(api.billing.checkout({ planKey, purpose: 'billing' }), `plan:${planKey}`)}
        />
      </section>

      {listedTopUps(plans).length > 0 && (
        <section className="card-list">
          <h2 className="section-title">Buy more credits</h2>
          <p className="hint-text">A one-time top-up, on top of whatever plan you're already on.</p>
          <div className="picker">
            {listedTopUps(plans).map((pack) => (
              <button
                type="button"
                key={pack.lookupKey}
                className="ghost"
                disabled={!pack.stripePriceId || busy === `topup:${pack.lookupKey}`}
                onClick={() => goTo(
                  api.billing.checkout({ topUpKey: pack.lookupKey, purpose: 'billing' }),
                  `topup:${pack.lookupKey}`,
                )}
              >
                {busy === `topup:${pack.lookupKey}` ? 'Redirecting…' : `${pack.name} — ${usd(pack.priceUsd)}`}
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="card-list">
        <h2 className="section-title">Change plan</h2>
        <button
          type="button"
          className="ghost"
          disabled={!billing.hasStripeCustomer || busy === 'portal'}
          onClick={() => goTo(api.billing.portal(), 'portal')}
        >
          {busy === 'portal' ? 'Redirecting…' : 'Change plan'}
        </button>
        <p className="hint-text">
          {billing.hasStripeCustomer
            ? 'Opens Stripe\'s customer portal, where you can change plan, update the card, or cancel. AmazAI does not collect a card number.'
            : 'A paid subscription has a Stripe customer. Explore does not. Choose a paid plan to open the portal.'}
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
