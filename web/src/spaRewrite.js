/**
 * Amplify Hosting rewrite so a client route is index.html with status 200.
 *
 * A rule whose status is `404` (or the console default `/<*>` → index.html)
 * still answers 404. That is the `/welcome/` probe: body is the SPA, status
 * is not. `200` is a rewrite, so the browser keeps the URL and reload does
 * not look like a missing page.
 *
 * The regex is Amplify's documented SPA rule: paths, not files with a static
 * extension. `apply-amplify-spa-rewrite.mjs` merges it onto the existing app
 * and does not replace the rule list, so a 301 already on the app survives.
 */
export const SPA_REWRITE_SOURCE = '</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>';

export const SPA_REWRITE = {
  source: SPA_REWRITE_SOURCE,
  target: '/index.html',
  status: '200',
  condition: null,
};

function field(rule, lower, upper) {
  if (!rule) return '';
  return rule[lower] ?? rule[upper] ?? '';
}

/** The catch-all that serves index.html as an error document, not a 301. */
export function isSpaIndexFallback(rule) {
  const target = field(rule, 'target', 'Target');
  if (target !== '/index.html') return false;
  const status = String(field(rule, 'status', 'Status'));
  const source = field(rule, 'source', 'Source');
  if (status === '404' || status === '404-200') return true;
  return source === SPA_REWRITE_SOURCE || source === '/<*>';
}

/**
 * Drop only the index.html catch-all, then append one 200 rewrite.
 * Every other rule stays, in its original order.
 */
export function mergeSpaRewrite(existing) {
  const kept = [];
  for (const rule of existing || []) {
    if (!isSpaIndexFallback(rule)) kept.push(rule);
  }
  kept.push({ ...SPA_REWRITE });
  return kept;
}
