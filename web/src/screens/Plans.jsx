import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import Logo from '../components/Logo';
import PlanCards from '../components/PlanCards';
import { api } from '../api';
import { friendly } from '../lib/errors';
import {
  TIER_LABEL, followBillingUrl, formatPlanPrice, homePath, orderedPlans, planEntitled,
} from '../lib/billing';
import { rememberRegistrationComplete } from '../hooks/useRegistration';

/**
 * Plan picker after Auth0, before the first Bot.
 *
 * Paid plans start the existing checkout. Explore is confirmed here, with
 * no card. Abandoning Checkout leaves the account incomplete; this page is
 * where that visit resumes.
 */
export default function Plans() {
  const location = useLocation();
  const navigate = useNavigate();
  const cancelled = new URLSearchParams(location.search).get('checkout') === 'cancelled';
  const [billing, setBilling] = useState(null);
  const [plans, setPlans] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    let live = true;
    Promise.all([api.billing.get(), api.billing.plans()])
      .then(([account, catalog]) => {
        if (!live) return;
        setBilling(account);
        setPlans(catalog);
      })
      .catch((err) => live && setError(friendly(err, "Couldn't load plans.")));
    return () => { live = false; };
  }, []);

  async function confirmExplore() {
    setBusy('plan:explore');
    setError('');
    try {
      const next = await api.billing.confirmExplore();
      setBilling(next);
      rememberRegistrationComplete();
      navigate(homePath(location.search));
    } catch (err) {
      setError(friendly(err, "Couldn't select Explore."));
      setBusy('');
    }
  }

  async function checkout(planKey) {
    setBusy(`plan:${planKey}`);
    setError('');
    try {
      const { url } = await api.billing.checkout({ planKey, purpose: 'signup' });
      followBillingUrl(url, navigate);
    } catch (err) {
      setError(friendly(err, "Couldn't start checkout."));
      setBusy('');
    }
  }

  const catalog = orderedPlans(plans);
  const entitled = planEntitled(billing);
  const trialBalance = billing?.tier === 'trial' ? billing.balanceUsd : null;

  return (
    <div className="plans-page">
      <div className="plans-page-inner">
        <Logo size={32} title="AmazAI" />
        <h1>Choose a plan</h1>
        <p className="plans-lead">
          Explore is free and does not ask for a card. Paid plans continue on Stripe Checkout.
          AmazAI never collects a card number.
        </p>
        {cancelled && (
          <div className="err">
            <span className="msg-text">Checkout was cancelled. No charge was made. Pick a plan when you are ready.</span>
          </div>
        )}
        {error && <div className="err"><span className="msg-text">{error}</span></div>}
        {trialBalance != null && (
          <p className="hint-text">
            A trial balance of {formatPlanPrice(trialBalance)} is already on this account.
            Choosing a plan does not remove it.
          </p>
        )}
        {!plans && !error && <p className="hint-text">Loading plans…</p>}
        {catalog.length > 0 && (
          <PlanCards
            plans={catalog}
            currentTier={entitled ? billing.tier : ''}
            busy={busy}
            subscribed={false}
            onExplore={confirmExplore}
            onCheckout={checkout}
          />
        )}
        {entitled && (
          <p className="plans-continue">
            <button type="button" className="primary" onClick={() => navigate(homePath(location.search))}>
              Continue
            </button>
          </p>
        )}
        <p className="hint-text">
          Paid plans renew monthly until canceled. By continuing you agree to the{' '}
          <Link to="/terms">Terms of Use</Link> and the{' '}
          <Link to="/billing-policy">Billing, Cancellation &amp; Refund Policy</Link>.
        </p>
      </div>
    </div>
  );
}

/**
 * Stripe returns here after a signup checkout. The workspace stays closed
 * until the webhook has named the plan. A slow webhook shows a wait, not a
 * first Bot.
 */
export function PlanSuccess() {
  const location = useLocation();
  const navigate = useNavigate();
  const [phase, setPhase] = useState('confirming');
  const [billing, setBilling] = useState(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    let timer = 0;
    let tries = 0;

    async function tick() {
      try {
        const row = await api.billing.get();
        if (!live) return;
        if (planEntitled(row)) {
          setBilling(row);
          setPhase('ready');
          rememberRegistrationComplete();
          return;
        }
      } catch (err) {
        if (!live) return;
        setError(friendly(err, "Couldn't confirm the payment."));
        setPhase('waiting');
        return;
      }
      tries += 1;
      if (!live) return;
      if (tries >= 8) {
        setPhase('waiting');
        return;
      }
      timer = window.setTimeout(tick, 2000);
    }

    setPhase('confirming');
    setError('');
    tick();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [attempt]);

  const planName = TIER_LABEL[billing?.tier] || 'Your plan';

  return (
    <div className="plans-page">
      <div className="plans-page-inner">
        <Logo size={32} title="AmazAI" />
        <h1>{phase === 'ready' ? 'Plan confirmed' : 'Confirming payment'}</h1>
        {phase === 'ready' ? (
          <>
            <p className="plans-lead">
              {planName} is on this account. You can meet your first Bot next.
            </p>
            <button type="button" className="primary" onClick={() => navigate(homePath(location.search))}>
              Continue
            </button>
          </>
        ) : (
          <>
            <p className="plans-lead">
              {phase === 'waiting'
                ? 'Stripe has not confirmed this payment yet. The workspace stays closed until it does.'
                : 'Waiting for Stripe to confirm this payment.'}
            </p>
            {error && <div className="err"><span className="msg-text">{error}</span></div>}
            {phase === 'waiting' && (
              <button type="button" className="ghost" onClick={() => setAttempt((n) => n + 1)}>
                Check again
              </button>
            )}
            <p className="hint-text">
              <Link to={{ pathname: '/plans', search: location.search }}>Back to plans</Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
