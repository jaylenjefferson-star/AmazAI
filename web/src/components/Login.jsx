import { useState } from 'react';
import { signIn } from '../auth';

export default function Login({ onDone }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfa, setMfa] = useState(null);      // { respond }
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const result = await signIn(email, password);
      if (result.mfa) setMfa(result); else onDone();
    } catch (err) {
      setError(err.message || 'Sign-in failed');
    } finally { setBusy(false); }
  }

  async function submitCode(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    try { await mfa.respond(code.trim()); onDone(); }
    catch (err) { setError(err.message || 'That code was not accepted'); }
    finally { setBusy(false); }
  }

  return (
    <div className="login">
      {!mfa ? (
        <form onSubmit={submit}>
          <h1>AmazAI</h1>
          <p>Sign in to your control plane.</p>
          {error && <div className="err">{error}</div>}
          <input type="email" placeholder="Email" autoComplete="username"
                 value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input type="password" placeholder="Password" autoComplete="current-password"
                 value={password} onChange={(e) => setPassword(e.target.value)} required />
          <button className="primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      ) : (
        <form onSubmit={submitCode}>
          <h1>Verification</h1>
          <p>Enter the 6-digit code from your authenticator app.</p>
          {error && <div className="err">{error}</div>}
          <input inputMode="numeric" autoFocus placeholder="000000" maxLength={6}
                 value={code} onChange={(e) => setCode(e.target.value)} required />
          <button className="primary" disabled={busy || code.length < 6}>
            {busy ? 'Verifying…' : 'Verify'}
          </button>
        </form>
      )}
    </div>
  );
}
