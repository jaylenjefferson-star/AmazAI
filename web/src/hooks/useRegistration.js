import { useEffect, useState } from 'react';
import { api } from '../api';
import { registrationGate } from '../lib/billing';

/**
 * Has this account picked a plan?
 *
 * Provisional, and deliberately not a lock. The server still answers every
 * other route. Only an explicit `registrationIncomplete: true` sends the
 * console to /plans. If billing cannot be read, the workspace stays open.
 */

export const CHECKING = 'checking';
export const INCOMPLETE = 'incomplete';
export const OPEN = 'open';

let resolved = null;
const listeners = new Set();

function publish(next) {
  resolved = next;
  listeners.forEach((fn) => fn(next));
}

export function rememberRegistrationComplete() {
  publish(OPEN);
}

export function forgetRegistration() {
  resolved = null;
}

async function resolve() {
  try {
    const billing = await api.billing.get();
    return registrationGate(billing) === 'incomplete' ? INCOMPLETE : OPEN;
  } catch {
    return OPEN;
  }
}

export function useRegistration() {
  const [state, setState] = useState(resolved ?? CHECKING);

  useEffect(() => {
    listeners.add(setState);
    if (resolved) {
      setState(resolved);
      return () => { listeners.delete(setState); };
    }
    let live = true;
    resolve().then((next) => {
      if (live && resolved !== OPEN) publish(next);
    });
    return () => { live = false; listeners.delete(setState); };
  }, []);

  return state;
}
