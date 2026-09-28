/**
 * Public routes have one source of truth.
 *
 * Amplify canonicalizes some direct requests with a trailing slash. Comparing
 * location.pathname to a list of slashless strings made those requests render
 * the signed-out homepage even though React Router had a matching policy page.
 */
export const PUBLIC_PATHS = [
  '/welcome-to-amazai', '/about', '/terms', '/privacy', '/cookie-policy',
  '/acceptable-use', '/security', '/security-disclosure',
  '/security-responsible-disclosure', '/billing-policy', '/subprocessors',
  '/ai-transparency', '/data-processing-addendum', '/how-it-works', '/product',
  '/pricing', '/integrations', '/use-cases', '/for-teams', '/enterprise', '/faq',
  '/contact', '/legal',
];

export function normalizePublicPath(pathname = '/') {
  if (pathname === '/') return pathname;
  return pathname.replace(/\/+$/, '') || '/';
}

export function isPublicPath(pathname) {
  return PUBLIC_PATHS.includes(normalizePublicPath(pathname));
}
