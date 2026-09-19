import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import Login from './components/Login';
import { isSignedIn } from './auth';
import './styles.css';

function Root() {
  const [state, setState] = useState('checking');

  useEffect(() => {
    isSignedIn().then((ok) => setState(ok ? 'in' : 'out'));
  }, []);

  if (state === 'checking') return null;
  if (state === 'out') return <Login onDone={() => setState('in')} />;
  return <App />;
}

createRoot(document.getElementById('root')).render(
  <StrictMode><Root /></StrictMode>,
);
