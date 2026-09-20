import { useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Where is this account in getting a first Bot?
 *
 * Setup is a fact about the account, not the browser: signing in from a second
 * machine must not replay it. So it is resolved from what the server holds --
 * and specifically from whether the account has a **first Bot** (an agent the
 * API flagged `entrypoint`), not from whether it has *any* agent.
 *
 * That distinction is the bug this replaced. `resolve` used to read "at least
 * one agent exists" as "this account is set up". A deployed stack always has
 * one -- `provision_agents.py` creates the Engineering seat before the owner
 * ever signs in -- so a brand-new owner skipped setup entirely, landed in an
 * inbox holding only Engineering, and their first conversation was
 * `/agents/eng`. Nothing in the account was ever a place to start.
 *
 *   NEEDED   nobody is here and setup never ran: walk through it
 *   OFFER    the account is in use (agents, or setup once ran) but has no
 *            first Bot: leave the inbox alone and offer one in it
 *   DONE     there is a first Bot, or the control plane could not be asked
 *
 * OFFER is deliberately not a wall. An owner with a working org and no first
 * Bot -- the Engineering-only stack, or an account whose setup predates first
 * Bots -- must still be able to reach their agents.
 *
 * Resolved once per page load, not once per navigation: the guard sits on a
 * layout route, and re-asking on every route change would put a request in
 * front of every screen to answer a question whose answer cannot change
 * without this tab doing the changing.
 */

export const CHECKING = 'checking';
export const NEEDED = 'needed';
export const OFFER = 'offer';
export const DONE = 'done';

let resolved = null;
const listeners = new Set();

function set(next) {
  resolved = next;
  listeners.forEach((fn) => fn(next));
}

/** Called once a first Bot exists, so anything showing the offer lets go of it. */
export function rememberSetupDone() {
  set(DONE);
}

export function forgetSetupState() {
  resolved = null;
}

async function resolve() {
  const [settings, agents] = await Promise.allSettled([api.settings(), api.agents()]);

  // Neither question could be asked. Whatever is wrong, it is not "this
  // person is new" -- and sending an owner into setup because the control
  // plane is unreachable is the failure this whole module exists to stop.
  // Let them through; the screen they land on reports the outage itself.
  if (settings.status === 'rejected' && agents.status === 'rejected') return DONE;

  const list = agents.status === 'fulfilled' ? (agents.value?.agents || []) : [];
  if (list.some((a) => a.entrypoint)) return DONE;

  const onboardedAt = settings.status === 'fulfilled' ? settings.value?.onboardedAt : null;
  if (list.length > 0 || onboardedAt) return OFFER;

  return NEEDED;
}

export function useFirstRun() {
  const [state, setState] = useState(resolved ?? CHECKING);

  useEffect(() => {
    listeners.add(setState);
    if (resolved) {
      setState(resolved);
      return () => { listeners.delete(setState); };
    }
    let live = true;
    resolve().then((next) => {
      // A first Bot made while this was in flight wins over a stale answer.
      if (live && resolved !== DONE) set(next);
    });
    return () => { live = false; listeners.delete(setState); };
  }, []);

  return state;
}
