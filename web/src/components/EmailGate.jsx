import { useEffect, useState } from 'react';
import { api } from '../api';
import { config, startLogout, useAuth0 } from '../auth0';
import Companion from '../characters/Companion';
import Logo from './Logo';

export const EMAIL_NOT_VERIFIED = 'EMAIL_NOT_VERIFIED';

/**
 * The first authenticated call creates the tenant row. The API answers 403
 * EMAIL_NOT_VERIFIED instead of creating one when the email is not verified.
 * This screen is that answer; the check itself is identity.ensure_verified_email.
 */
export default function EmailGate({ children }) {
  const { getAccessTokenSilently, logout } = useAuth0();
  const [phase, setPhase] = useState('checking');

  useEffect(() => {
    let live = true;
    api.settings()
      .then(() => { if (live) setPhase('ok'); })
      .catch((err) => { if (live) setPhase(err?.code === EMAIL_NOT_VERIFIED ? 'unverified' : 'ok'); });
    return () => { live = false; };
  }, []);

  async function recheck() {
    setPhase('checking');
    try {
      await getAccessTokenSilently({
        cacheMode: 'off',
        authorizationParams: config.audience ? { audience: config.audience } : {},
      });
    } catch {
      // A failed refresh still falls through to the API, which is the gate.
    }
    try {
      await api.settings();
      setPhase('ok');
    } catch (err) {
      setPhase(err?.code === EMAIL_NOT_VERIFIED ? 'unverified' : 'ok');
    }
  }

  if (phase === 'ok') return children;

  if (phase === 'unverified') {
    return (
      <div className="authgate">
        <div className="authgate-card">
          <Logo size={34} title="AmazAI" />
          <Companion archetype="lantern" color="#2b6bff" state="waiting" size={84} />
          <h1>Verify your email</h1>
          <p>
            Open the message from Auth0 and confirm your address. AmazAI creates
            your workspace only after that confirmation. If you already confirmed
            it, continue so we can check again.
          </p>
          <button className="primary" type="button" onClick={recheck}>I&apos;ve verified my email</button>
          <button type="button" onClick={() => startLogout(logout)}>Sign out</button>
        </div>
      </div>
    );
  }

  return (
    <div className="authgate">
      <div className="authgate-card">
        <Logo size={34} title="AmazAI" />
        <Companion archetype="lantern" color="#2b6bff" state="thinking" size={84} />
        <h1>Just a moment</h1>
        <p>Checking your session.</p>
      </div>
    </div>
  );
}
