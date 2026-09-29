import { startLogin, useAuth0 } from '../../auth0';
import { SIGNUP_RETURN } from '../../lib/billing';

/**
 * The closing call to action. Deliberately spare: a lot of whitespace, one
 * headline, one supporting line, one button. Copy is verbatim from the spec.
 * Kept visually louder than the footer that follows so the page ends on the
 * invitation, not on a sitemap.
 */
export default function FinalCta() {
  const { loginWithRedirect } = useAuth0();
  return (
    <section className="mkt-section mkt-final">
      <div className="mkt-container mkt-final-inner">
        <h2 className="mkt-final-title">Build your AI team.</h2>
        <p className="mkt-final-sub">
          Start with one companion. Give it a job. See what happens next.
        </p>
        <button
          className="mkt-btn mkt-btn-primary mkt-final-cta"
          onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: SIGNUP_RETURN })}
        >
          Start free
        </button>
      </div>
    </section>
  );
}
