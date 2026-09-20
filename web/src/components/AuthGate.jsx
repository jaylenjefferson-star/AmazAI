import Companion from '../characters/Companion';
import Logo from './Logo';
import { configured, isOwner, startLogin, startLogout, useAuth0 } from '../auth0';
import { DEMO } from '../demo';

/**
 * The boundary between "anyone" and "the owner".
 *
 * It gates the interface and nothing else. Hiding a route in a browser is
 * not authorization: the API has to validate the access token itself, which
 * is what `services/amazai/identity.py` does. If this component were the
 * only check, every protected thing here would be one devtools session away
 * from being unprotected.
 */
export default function AuthGate({ children }) {
  const { isLoading, isAuthenticated, error, user, loginWithRedirect, logout } = useAuth0();

  // Demo mode renders the console against fixtures, so there is no session to
  // check and nothing behind this gate to protect -- `demoApi` never touches
  // the control plane. `DEMO` is `import.meta.env.DEV && ?demo`, which a
  // production build folds to `false`; the gate is unchanged where it matters.
  if (DEMO) return children;

  if (!configured) {
    return (
      <Screen
        state="blocked"
        title="Sign-in is not configured"
        body="This build has no Auth0 domain or client ID, so there is nothing to sign in to."
        hint="Set VITE_AUTH0_DOMAIN and VITE_AUTH0_CLIENT_ID and rebuild."
      />
    );
  }

  if (isLoading) {
    return (
      <Screen state="thinking" title="Just a moment" body="Checking your session." quiet />
    );
  }

  if (error) {
    return (
      <Screen
        state="blocked"
        title="That sign-in did not complete"
        body={error.message}
        action={{ label: 'Try again', onClick: () => startLogin(loginWithRedirect) }}
      />
    );
  }

  if (!isAuthenticated) {
    return (
      <Screen
        state="idle"
        title="Welcome back"
        body="Sign in to reach your companions."
        action={{ label: 'Sign in', onClick: () => startLogin(loginWithRedirect), primary: true }}
      />
    );
  }

  if (!isOwner(user)) {
    // Deliberately specific about what is wrong and unhelpful about how to
    // get around it.
    return (
      <Screen
        state="blocked"
        title="This workspace is not yours"
        body={`Signed in as ${user?.email || user?.sub}, which is not the owner of this workspace.`}
        action={{ label: 'Sign out', onClick: () => startLogout(logout) }}
      />
    );
  }

  return children;
}

function Screen({ state, title, body, hint, action, quiet }) {
  return (
    <div className="authgate">
      <div className="authgate-card">
        <Logo size={34} title="AmazAI" />
        <Companion archetype="lantern" color="#2b6bff" state={state} size={84} />
        <h1>{title}</h1>
        <p>{body}</p>
        {hint && <code>{hint}</code>}
        {action && (
          <button className={action.primary ? 'primary' : ''} onClick={action.onClick}>
            {action.label}
          </button>
        )}
        {quiet && <span className="authgate-quiet" aria-hidden="true" />}
      </div>
    </div>
  );
}
