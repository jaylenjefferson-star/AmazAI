/**
 * Plan picker rules shared by signup and the billing screen.
 *
 * Prices and credit amounts come from the billing API, which reads
 * services/amazai/billing_plans.json. This module does not list dollar
 * amounts. The two top-up keys are the packs that file already sells.
 */

export const PLAN_ORDER = ['explore', 'personal', 'personal_plus', 'pro', 'power'];

export const TIER_LABEL = {
  trial: 'Free trial',
  explore: 'Explore',
  personal: 'Personal',
  personal_plus: 'Personal+',
  pro: 'Pro',
  power: 'Power',
};

export const TOP_UP_KEYS = ['amazai_credits_50', 'amazai_credits_200'];

/** After Auth0, a new account lands on the plan picker. First Bot is later. */
export const SIGNUP_RETURN = '/plans';

export function formatPlanPrice(priceUsd) {
  const value = Number(priceUsd);
  if (!Number.isFinite(value)) return '';
  return value % 1 === 0 ? `$${value}` : `$${value.toFixed(2)}`;
}

export function orderedPlans(catalog) {
  const plans = catalog?.plans || {};
  return PLAN_ORDER.filter((key) => plans[key]).map((key) => ({ key, ...plans[key] }));
}

/** Only the two catalog packs. A payload that grows a third price is not shown. */
export function listedTopUps(catalog) {
  return (catalog?.creditTopUps || []).filter((row) => TOP_UP_KEYS.includes(row.lookupKey));
}

/**
 * Gate the console only when the account explicitly has not picked a plan.
 * A missing flag, a failed read, or an older row stays open — billing being
 * unread must not wall the workspace.
 */
export function registrationGate(billing) {
  return billing?.registrationIncomplete === true ? 'incomplete' : 'open';
}

/** Paid checkout or Explore confirm has named a catalog plan. Trial has not. */
export function planEntitled(billing) {
  if (!billing || billing.registrationIncomplete === true) return false;
  return PLAN_ORDER.includes(billing.tier);
}

export function plansPath(search = '') {
  return withParams('/plans', search);
}

export function homePath(search = '') {
  return withParams('/', search);
}

function withParams(path, search) {
  const params = new URLSearchParams(String(search || '').replace(/^\?/, ''));
  params.delete('checkout');
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

/**
 * Stripe URLs leave the app. A same-origin path (the demo signup return,
 * and any future in-app return) stays inside the router and keeps the
 * current query, so `?demo=1` survives the hop.
 */
export function followBillingUrl(url, navigate) {
  if (typeof url !== 'string' || !url) return;
  if (url.startsWith('/')) {
    const next = appendCurrentSearch(url);
    if (typeof navigate === 'function') navigate(next);
    else window.location.assign(next);
    return;
  }
  window.location.assign(url);
}

function appendCurrentSearch(url) {
  if (typeof window === 'undefined') return url;
  const current = new URLSearchParams(window.location.search);
  current.delete('checkout');
  const [path, query = ''] = url.split('?');
  const params = new URLSearchParams(query);
  current.forEach((value, key) => {
    if (!params.has(key)) params.set(key, value);
  });
  const next = params.toString();
  return next ? `${path}?${next}` : path;
}
