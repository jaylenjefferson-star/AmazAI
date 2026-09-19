import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { applyMode, MODES, nextMode, storedMode } from '../theme';
import { config, startLogout, useAuth0 } from '../auth0';
import { DEMO_DATA } from '../fixtures';

const LABEL = { light: 'Light', dark: 'Dark', system: 'Match system' };

export default function Settings() {
  const { user, logout } = useAuth0();
  const [mode, setMode] = useState(storedMode);
  useEffect(() => { applyMode(mode); }, [mode]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
        <p>This workspace, and how it looks.</p>
      </header>

      <section className="settings-group">
        <h2 className="section-title">Appearance</h2>
        <div className="setting-row">
          <div>
            <strong>Theme</strong>
            <span>AmazAI is a light product; dark is available.</span>
          </div>
          <div className="seg">
            {MODES.map((m) => (
              <button key={m} type="button" className={mode === m ? 'on' : ''}
                      onClick={() => setMode(m)}>{LABEL[m]}</button>
            ))}
          </div>
        </div>
      </section>

      <section className="settings-group">
        <h2 className="section-title">Account</h2>
        <div className="setting-row">
          <div>
            <strong>{user?.name || user?.email}</strong>
            <span>{user?.email} · {user?.sub}</span>
          </div>
          <button className="danger" onClick={() => startLogout(logout)}>Sign out</button>
        </div>
        <div className="setting-row">
          <div>
            <strong>Identity provider</strong>
            <span>Auth0 · {config.domain}</span>
          </div>
        </div>
      </section>

      <section className="settings-group">
        <h2 className="section-title">Environment</h2>
        <div className="setting-row">
          <div>
            <strong>Data source</strong>
            <span>
              {DEMO_DATA
                ? 'Demo fixtures. No control plane is connected, so nothing here is real.'
                : 'Live control plane.'}
            </span>
          </div>
          <span className={`state-chip ${DEMO_DATA ? 'cc-tone-warn' : 'cc-tone-ok'}`}>
            <i className="cc-dot" aria-hidden="true" />
            {DEMO_DATA ? 'Demo' : 'Live'}
          </span>
        </div>
        <div className="setting-row">
          <div>
            <strong>Companion gallery</strong>
            <span>Every character and state on one screen.</span>
          </div>
          <Link className="btn-link" to="/characters">Open</Link>
        </div>
      </section>
    </div>
  );
}
