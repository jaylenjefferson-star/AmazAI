import { isPublicPath } from './publicRoutes';

// Preserve the existing theme-module export for callers while keeping the
// route list itself in one shared module.
export { isPublicPath } from './publicRoutes';

/**
 * Theme selection: 'system' (the default), 'light' or 'dark'.
 *
 * 'system' is a real third state, not a synonym for whichever one is showing.
 * A console that is read at 2am and again at noon should follow the machine
 * unless its owner has said otherwise, so the stored value distinguishes
 * "no preference" from "light, and I mean it".
 *
 * The attribute goes on <html>, which is also where an inline script in
 * index.html sets it before first paint to avoid a flash of the wrong theme.
 */

const KEY = 'amazai.theme';
export const MODES = ['light', 'dark', 'system'];

/** What the signed-in app shows until its owner chooses otherwise: dark.
 *
 *  The public site is a different case and stays light (see `modeForPath`): a
 *  visitor on a dark-mode laptop should see the brand as designed rather than a
 *  dark variant they never asked for. 'system' remains available in both. */
export const DEFAULT_MODE = 'dark';

export function storedMode() {
  try {
    const v = localStorage.getItem(KEY);
    return MODES.includes(v) ? v : DEFAULT_MODE;
  } catch {
    // Private browsing, blocked site data: fall back rather than throw.
    return DEFAULT_MODE;
  }
}

export function applyMode(mode) {
  const root = document.documentElement;
  if (mode === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  try { localStorage.setItem(KEY, mode); } catch { /* not worth failing over */ }
}

/** What is actually on screen right now, with 'system' resolved. */
export function effectiveMode(mode) {
  if (mode !== 'system') return mode;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function nextMode(mode) {
  return MODES[(MODES.indexOf(mode) + 1) % MODES.length];
}

/** A preference the person actually set, or null. Distinct from the default. */
export function explicitMode() {
  try {
    const v = localStorage.getItem(KEY);
    return MODES.includes(v) ? v : null;
  } catch {
    return null;
  }
}

/** Auth0 caches its session in localStorage, so a signed-in visitor can be told
 *  from a signed-out one before the SDK has finished loading. */
export function hasSessionHint() {
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      if ((localStorage.key(i) || '').indexOf('@@auth0spajs@@') === 0) return true;
    }
  } catch { /* blocked storage: treat as signed out */ }
  return false;
}

/** The mode a path should show. The signed-in app is dark unless they chose;
 *  anything a signed-out visitor can see is light unless they chose dark/system. */
export function modeForPath(pathname, signedIn = hasSessionHint()) {
  const chosen = explicitMode();
  if (isPublicPath(pathname) || !signedIn) {
    return chosen === 'dark' || chosen === 'system' ? chosen : 'light';
  }
  return chosen ?? 'dark';
}

/** Show that mode without persisting it: navigating is not a preference. */
export function syncToPath(pathname, signedIn) {
  const mode = modeForPath(pathname, signedIn);
  const root = document.documentElement;
  if (mode === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
}
