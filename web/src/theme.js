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

/** Light unless the viewer has chosen otherwise.
 *
 *  Deliberately not 'system': AmazAI is a light product, and a visitor
 *  arriving on a dark-mode laptop should see the brand as designed rather
 *  than a dark variant they never asked for. 'system' remains available,
 *  it is just no longer the default. */
export const DEFAULT_MODE = 'light';

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
