import { useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Has this account finished first-run setup?
 *
 * This used to be `localStorage['amazai.onboarded']`, which made setup a fact
 * about the browser rather than about the account. Signing in from a second
 * machine, a private window, a different browser, or the same one after
 * clearing site data all produced an account that had been set up months ago
 * being walked through setup again -- and, because the old setup screen wrote
 * nothing to the server, being walked through it again the next time too.
 *
 * So it is resolved from state the server actually holds:
 *
 *   settings.onboardedAt   setup was completed, and when
 *   at least one agent     the account is plainly in use, whether or not it
 *                          ever went through a screen that recorded it
 *
 * The second clause is backfill. No account can have an `onboardedAt` from
 * before the field existed, and sending an owner with a working org through
 * setup would be the same bug wearing a different hat.
 *
 * Resolved once per page load, not once per navigation: the guard sits on a
 * layout route, and re-asking on every route change would put a request in
 * front of every screen to answer a question whose answer cannot change
 * without this tab doing the changing.
 */

export const CHECKING = 'checking';
export const NEEDED = 'needed';
export const DONE = 'done';

let resolved = null;

/** Called by setup when it has actually written its result. */
export function rememberSetupDone() {
  resolved = DONE;
}

export function forgetSetupState() {
  resolved = null;
}

async function resolve() {
  const [settings, agents] = await Promise.allSettled([api.settings(), api.agents()]);

  if (settings.status === 'fulfilled' && settings.value?.onboardedAt) return DONE;
  if (agents.status === 'fulfilled' && (agents.value?.agents || []).length > 0) return DONE;

  // Neither question could be asked. Whatever is wrong, it is not "this
  // person is new" -- and sending an owner into setup because the control
  // plane is unreachable is the failure this whole module exists to stop.
  // Let them through; the screen they land on reports the outage itself.
  if (settings.status === 'rejected' && agents.status === 'rejected') return DONE;

  return NEEDED;
}

export function useFirstRun() {
  const [state, setState] = useState(resolved ?? CHECKING);

  useEffect(() => {
    if (resolved) return undefined;
    let live = true;
    resolve().then((next) => {
      resolved = next;
      if (live) setState(next);
    });
    return () => { live = false; };
  }, []);

  return state;
}
