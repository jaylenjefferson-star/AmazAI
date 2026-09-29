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

/**
 * Fixed client routes copied to `<route>/index.html` at build time.
 *
 * Amplify redirects `/welcome` to `/welcome/` and, with no object there,
 * CloudFront answers 404 whose body happens to be index.html. A reload of
 * that 404 restarts the wizard. These copies make the trailing-slash URL
 * a real 200. Dynamic ids (`/agents/:id`) still need the SPA rewrite rule
 * in `spaRewrite.js` — a directory per id cannot be generated ahead of time.
 *
 * Kept off PUBLIC_PATHS on purpose: `/welcome` is signed-in setup, not a
 * marketing page.
 */
export const SPA_ENTRY_PATHS = [
  '/welcome',
  '/agents',
  '/agents/new',
  '/marketplace',
  '/connectors',
  '/rooms',
  '/org',
  '/routines',
  '/routines/new',
  '/artifacts',
  '/settings',
  '/usage',
  '/billing',
  '/plans',
  '/plans/success',
  '/characters',
  '/admin',
  '/admin/directory',
  '/admin/killswitch',
  '/admin/audit',
];

export function isWelcomePath(pathname) {
  return normalizePublicPath(pathname) === '/welcome';
}
