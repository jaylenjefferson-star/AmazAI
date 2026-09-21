import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { applyMode, MODES, storedMode } from '../theme';
import { config, startLogout, useAuth0 } from '../auth0';
import { api } from '../api';
import { RosterSkeleton } from './Skeleton';

/**
 * The account's own settings: what reaches you, and how the console looks.
 *
 * One body, two shells. The avatar opens it as a sheet over whatever you were
 * reading, which is the whole point of settings that are *contextual* rather
 * than a destination; `/settings` renders the same component as a page so a
 * deep link still resolves. Two implementations would be two places to change
 * one preference, and they would eventually disagree.
 *
 * Preferences decide what reaches a person, never what a companion may do --
 * that is `policy.py`. `approval` is deliberately absent from the notification
 * list: an approval is a question a run cannot proceed without, so a switch
 * for it would be a way to make the gate invisible rather than a way to be
 * less disturbed. The API refuses it by name; there is no control here to
 * refuse.
 */

const NOTIFY = [
  ['completion', 'Finished work', 'A run ends, successfully or not.'],
  ['inputNeeded', 'Needs your input', 'An agent is waiting on an answer.'],
  ['failure', 'Failures', 'A run stopped on an error rather than finishing.'],
];

const THEME_LABEL = { light: 'Light', dark: 'Dark', system: 'Match system' };

function Row({ title, note, children }) {
  return (
    <div className="setting-row">
      <div>
        <strong>{title}</strong>
        {note && <span>{note}</span>}
      </div>
      {children}
    </div>
  );
}

export default function AccountSettings({ onNavigate, anchor }) {
  useEffect(() => {
    if (anchor) document.getElementById(anchor)?.scrollIntoView({ block: 'start' });
  }, [anchor]);

  const { user, logout } = useAuth0();
  const [mode, setMode] = useState(storedMode);
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState('');
  const [zoneDraft, setZoneDraft] = useState('');
  const [nameDraft, setNameDraft] = useState('');

  useEffect(() => { applyMode(mode); }, [mode]);

  useEffect(() => {
    api.settings()
      .then((s) => {
        setSettings(s);
        setZoneDraft(s.defaultTimezone || '');
        setNameDraft(s.workspaceName || '');
        // The server is the durable record; localStorage is only what makes
        // the first paint correct. A theme chosen on another device arrives
        // here, and a viewer who has never chosen keeps the local default
        // rather than being overridden by a value nobody set.
        if (s.theme && s.theme !== mode) {
          setMode(s.theme);
        }
      })
      .catch((e) => setError(e.message));
    // Once, on open. Re-reading on every theme change would fight the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Partial, always: a group that is not on screen must not be reset by a
   *  change to one that is. */
  async function patch(changes) {
    setError('');
    const previous = settings;
    setSettings((s) => ({ ...s, ...changes }));   // optimistic
    try {
      setSettings(await api.saveSettings(changes));
    } catch (e) {
      setSettings(previous);                      // and honest when refused
      setError(e.message);
    }
  }

  const notifications = settings?.notifications || {};

  return (
    <>
      <section className="settings-group">
        <h2 className="section-title" id="acct-account">Signed in</h2>
        {/* Never "Signed in / Signed in": the group already says that, so the
            row falls back to naming the thing rather than repeating it. */}
        <Row title={user?.name || user?.email || 'This account'}
             note={[user?.email || user?.sub, config.domain && `Auth0 · ${config.domain}`]
               .filter(Boolean).join(' · ')}>
          <button className="danger" onClick={() => startLogout(logout)}>Sign out</button>
        </Row>
      </section>

      <section className="settings-group">
        <h2 className="section-title" id="acct-org">Workspace</h2>
        {/* Named during setup, and changeable here. It is stored on the
            account rather than in this browser, which is the whole reason
            signing in somewhere new no longer looks like a new account. */}
        <Row title="Name" note="What this workspace is called.">
          <input className="inline-input" value={nameDraft} placeholder="My workspace"
                 maxLength={60} aria-label="Workspace name"
                 onChange={(e) => setNameDraft(e.target.value)}
                 onBlur={() => {
                   const next = nameDraft.trim();
                   if (next !== (settings?.workspaceName || '')) {
                     patch({ workspaceName: next || null });
                   }
                 }} />
        </Row>
      </section>

      <section className="settings-group">
        <h2 className="section-title" id="acct-prefs">Notifications</h2>
        {!settings && <RosterSkeleton rows={2} />}
        {settings && NOTIFY.map(([key, title, note]) => (
          <Row key={key} title={title} note={note}>
            <label className="native-toggle">
              <input type="checkbox" checked={notifications[key] !== false}
                     onChange={(e) => patch({ notifications: { [key]: e.target.checked } })} />
              <span aria-hidden="true" />
              <span className="sr-only">{title}</span>
            </label>
          </Row>
        ))}
        {settings && (
          <p className="setgroup-note">
            An approval is not on this list. A run cannot continue without one,
            so it is a question rather than a notification.
          </p>
        )}
      </section>

      <section className="settings-group">
        <h2 className="section-title" id="acct-look">Appearance</h2>
        <Row title="Theme" note="Dark by default. Light and match-system are here if you prefer them.">
          <div className="seg">
            {MODES.map((m) => (
              <button key={m} type="button" className={mode === m ? 'on' : ''}
                      onClick={() => { setMode(m); patch({ theme: m }); }}>
                {THEME_LABEL[m]}
              </button>
            ))}
          </div>
        </Row>
      </section>

      <section className="settings-group">
        <h2 className="section-title">Defaults</h2>
        <Row title="Timezone"
             note="Given to a new agent when you do not name one, so a schedule means the hour where you are.">
          <input className="inline-input" value={zoneDraft} placeholder="America/Los_Angeles"
                 aria-label="Default timezone"
                 onChange={(e) => setZoneDraft(e.target.value)}
                 onBlur={() => {
                   const next = zoneDraft.trim();
                   if (next !== (settings?.defaultTimezone || '')) {
                     patch({ defaultTimezone: next || null });
                   }
                 }} />
        </Row>
      </section>

      <section className="settings-group">
        <h2 className="section-title">This environment</h2>
        <Row title="Data source"
             note="Live AWS control plane. Empty sections mean no real records exist yet.">
          <span className="state-chip cc-tone-ok">
            <i className="cc-dot" aria-hidden="true" />Live
          </span>
        </Row>
        <Row title="Character gallery" note="Every character and state on one screen.">
          <Link className="btn-link" to="/characters" onClick={onNavigate}>Open</Link>
        </Row>
      </section>

      {error && <div className="err"><span className="msg-text">{error}</span></div>}
    </>
  );
}

/** The sheet the avatar opens, over whatever was being read. */
export function AccountSheet({ onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet sheet-tall" role="dialog" aria-modal="true" aria-label="Account">
        <div className="sheet-head">
          <h2 className="sheet-title">Account</h2>
          <button type="button" className="chat-icon" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <AccountSettings onNavigate={onClose} />
      </div>
    </>
  );
}
