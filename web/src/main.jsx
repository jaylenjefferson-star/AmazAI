import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import Login from './components/Login';
import { isSignedIn, configured } from './auth';
import { DEMO } from './demo';
import Logo from './components/Logo';
import './styles.css';

function Root() {
  const [state, setState] = useState('checking');

  useEffect(() => {
    if (DEMO) return setState('in');
    isSignedIn().then((ok) => setState(ok ? 'in' : 'out'));
  }, []);

  // A console served with an unfilled .env would otherwise present a sign-in
  // form that cannot succeed, and fail on submit rather than on sight.
  if (!DEMO && !configured) {
    return (
      <div className="login">
        <div className="empty" style={{ maxWidth: 440 }}>
          <Logo size={40} title="AmazAI" />
          <span className="title" style={{ marginTop: 6 }}>
            This console is not wired up yet
          </span>
          <span>
            It was built without the Cognito pool from the stack outputs, so
            there is nothing to sign in to.
          </span>
          <code>cp web/.env.example web/.env</code>
          <span>Fill it from <code>npx cdk deploy</code> outputs, then rebuild.</span>
        </div>
      </div>
    );
  }

  if (state === 'checking') return null;
  if (state === 'out') return <Login onDone={() => setState('in')} />;
  return <App />;
}

createRoot(document.getElementById('root')).render(
  <StrictMode><Root /></StrictMode>,
);
