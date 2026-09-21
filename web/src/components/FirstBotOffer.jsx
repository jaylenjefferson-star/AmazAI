import { useMemo, useState } from 'react';
import Companion from '../characters/Companion';
import { operatorFirstName, useAuth0 } from '../auth0';
import { api } from '../api';
import { rememberSetupDone } from '../hooks/useFirstRun';
import { COPY } from '../lib/errors';
import Problem from './Problem';

// A colour the API accepts (`agents.AVATAR_COLORS`) and one the Engineering
// seat's blue does not collide with, so the two read as different Bots at a
// glance in the same list.
const LOOK = { archetype: 'pebble', color: '#8b5cf6' };

/**
 * "Meet your first Bot", for an account that is already in use.
 *
 * The first-run screen handles an account with nobody in it. This handles the
 * other one: a stack that was deployed with its Engineering seat already
 * provisioned, whose owner therefore never saw setup and has had nothing but
 * Engineering to talk to. Walling that owner off from their agents until they
 * do a setup they were never shown would be the wrong fix, so the offer sits
 * at the top of the inbox and everything below it keeps working.
 *
 * One click, and it says what the click does. Making a Bot provisions a real
 * harness -- a billable AWS action -- so it is never done on load, and the
 * idempotency key means a double-click or a retry after a timeout lands on the
 * Bot the first attempt made rather than a second.
 */
export default function FirstBotOffer({ onCreated }) {
  const { user } = useAuth0();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const idempotencyKey = useMemo(
    () => `first-bot-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
    [],
  );

  async function meet() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const bot = await api.createAgent({
        name: 'Chief',
        entrypoint: true,
        operatorName: operatorFirstName(user),
        avatar: { shape: LOOK.archetype, color: LOOK.color },
      }, idempotencyKey);
      rememberSetupDone();
      onCreated(bot);
    } catch (err) {
      // Left in place with the reason. An offer that vanishes on failure reads
      // as though it worked.
      setError(err);
      setBusy(false);
    }
  }

  return (
    <div className="rs-offer">
      <button type="button" className="rs-row rs-row--offer" onClick={meet} disabled={busy}
              aria-label="Meet Chief, your first agent">
        <span className="rs-mark">
          <Companion archetype={LOOK.archetype} color={LOOK.color}
                     state={busy ? 'thinking' : 'idle'} size={48} name="Chief" />
        </span>
        <span className="rs-main">
          <span className="rs-line">
            <strong className="rs-name">Chief</strong>
            <span className="rs-chip">Chief of staff</span>
          </span>
          <span className="rs-line">
            <span className="rs-preview">{busy ? 'Setting up…' : 'Your first agent. Tap to meet.'}</span>
            <span className="rs-pill">{busy ? '…' : 'Meet'}</span>
          </span>
        </span>
      </button>
      {error && <Problem error={error} message={COPY.createAgent('Chief')} onRetry={meet} inline />}
    </div>
  );
}
