/**
 * Auth0 for the AmazAI console.
 *
 * This is a Vite React SPA, so the pattern is Universal Login with the
 * authorization-code flow and PKCE — never an embedded login form, and never
 * a client secret. A secret in a browser bundle is a published secret, and
 * `.env` files in a frontend build are not private either: everything here
 * ends up readable in the shipped JavaScript. Only public configuration
 * belongs in this file's inputs.
 *
 * **The backend is not protected by this.** The API today validates Cognito
 * JWTs, not Auth0 ones. Hiding a route in the browser hides a route in the
 * browser; it is not authorization. Until the API validates Auth0 access
 * tokens server-side, nothing here should be read as protecting real data,
 * which is why the console runs on fixtures in this environment.
 */

import { Auth0Provider, useAuth0 } from '@auth0/auth0-react';

export const config = {
  domain: import.meta.env.VITE_AUTH0_DOMAIN || '',
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID || '',
  // Only set once an API actually validates access tokens. Asking for an
  // audience that nothing checks yields a token nothing checks.
  audience: import.meta.env.VITE_AUTH0_AUDIENCE || '',
};

export const configured = Boolean(config.domain && config.clientId);

/** The owner-only seam. */
const OWNER_SUB = import.meta.env.VITE_OWNER_SUB || '';
const OWNER_EMAIL = (import.meta.env.VITE_OWNER_EMAIL || '').toLowerCase();

/**
 * Is this the account owner?
 *
 * A single place to change, so restricting the console to one subject later
 * is an edit here rather than a hunt. It matches on `sub` first because an
 * email can be re-registered and a subject cannot; email is a convenience
 * for before the sub is known.
 *
 * Returning true when neither is configured is deliberate: this is a
 * personal environment, and a half-configured owner check that locks the
 * owner out is worse than one that is plainly off. The real gate is
 * server-side and is not this.
 */
export function isOwner(user) {
  if (!user) return false;
  if (!OWNER_SUB && !OWNER_EMAIL) return true;
  if (OWNER_SUB && user.sub === OWNER_SUB) return true;
  if (OWNER_EMAIL && (user.email || '').toLowerCase() === OWNER_EMAIL) {
    // An unverified email is a claim, not an identity.
    return user.email_verified !== false;
  }
  return false;
}

export function AmazAIAuthProvider({ children }) {
  if (!configured) return children;

  return (
    <Auth0Provider
      domain={config.domain}
      clientId={config.clientId}
      authorizationParams={{
        redirect_uri: window.location.origin,
        ...(config.audience ? { audience: config.audience } : {}),
      }}
      // Rotating refresh tokens, so a reload does not bounce through a
      // hidden iframe — Safari's tracking prevention blocks that, and this
      // console has to survive a refresh on a phone.
      useRefreshTokens
      cacheLocation="localstorage"
      onRedirectCallback={(appState) => {
        // Return people to what they were opening, then drop Auth0's ?code=
        // and &state= without adding a history entry — Back must not return
        // to a spent authorization code.
        const target = appState?.returnTo || window.location.pathname;
        window.history.replaceState({}, document.title, target);
        // The router reads location on mount; a popstate makes it re-read
        // without a reload.
        window.dispatchEvent(new PopStateEvent('popstate'));
      }}
    >
      {children}
    </Auth0Provider>
  );
}

export { useAuth0 };


/**
 * Start Universal Login.
 *
 * `screen_hint: 'signup'` asks Auth0 to open its own signup screen rather
 * than AmazAI pretending to own a registration form. AmazAI never sees a
 * password, and building a form that looks like it might is worse than not
 * building one.
 */
export function startLogin(loginWithRedirect, { signup = false, returnTo } = {}) {
  return loginWithRedirect({
    appState: { returnTo: returnTo || window.location.pathname },
    authorizationParams: signup ? { screen_hint: 'signup' } : {},
  });
}

export function startLogout(logout) {
  return logout({ logoutParams: { returnTo: window.location.origin } });
}
