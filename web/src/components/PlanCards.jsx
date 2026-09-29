import { formatPlanPrice } from '../lib/billing';

/**
 * Catalog plans, rendered from the billing API payload.
 *
 * Explore is an in-app confirm. Every other plan goes to Stripe Checkout
 * unless this account already has a Stripe customer — then the plan change
 * happens in the Customer Portal, not a second subscription checkout.
 */
export default function PlanCards({ plans, currentTier, busy, subscribed, onExplore, onCheckout }) {
  return (
    <div className="pricing-grid">
      {plans.map((plan) => {
        const current = currentTier === plan.key;
        const featured = current || plan.key === 'personal_plus';
        const action = cardAction(plan, { current, subscribed, busy, onExplore, onCheckout });
        return (
          <article key={plan.key} className={featured ? 'pricing-card featured' : 'pricing-card'}>
            {current && <span className="pricing-badge">Current plan</span>}
            {!current && plan.key === 'personal_plus' && <span className="pricing-badge">Most popular</span>}
            <h2>{plan.name}</h2>
            <p className="pricing-price">
              <strong>{formatPlanPrice(plan.priceUsd)}</strong>
              {plan.interval ? `/${plan.interval}` : ''}
            </p>
            {plan.creditsPerMonth != null && (
              <p className="pricing-credits">{Number(plan.creditsPerMonth).toLocaleString()} Amaz Credits</p>
            )}
            <p className="pricing-tagline">{plan.description}</p>
            {action && (
              <button
                type="button"
                className={action.primary ? 'primary' : 'ghost'}
                disabled={action.disabled}
                onClick={action.onClick}
              >
                {action.label}
              </button>
            )}
          </article>
        );
      })}
    </div>
  );
}

function cardAction(plan, { current, subscribed, busy, onExplore, onCheckout }) {
  if (current) {
    return { label: 'Current plan', disabled: true, primary: false };
  }
  if (subscribed) return null;
  const key = `plan:${plan.key}`;
  if (plan.key === 'explore') {
    return {
      label: busy === key ? 'Confirming…' : 'Continue with Explore',
      disabled: busy === key,
      primary: false,
      onClick: () => onExplore?.(plan.key),
    };
  }
  if (!plan.stripePriceId) {
    return { label: 'Not available yet', disabled: true, primary: false };
  }
  return {
    label: busy === key ? 'Redirecting…' : 'Continue to checkout',
    disabled: busy === key,
    primary: plan.key === 'personal_plus',
    onClick: () => onCheckout?.(plan.key),
  };
}
