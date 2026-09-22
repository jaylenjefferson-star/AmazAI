import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { startLogout, useAuth0 } from '../auth0';
import { applyMode, storedMode } from '../theme';
import AccountSettings from './AccountSettings';
import Sheet, { SheetRow } from './Sheet';
import ToolsSheet from './ToolsSheet';
import UserAvatar from './UserAvatar';

const LOOKS = [['dark', 'Dark'], ['light', 'Light'], ['system', 'Auto']];

/**
 * What the avatar opens. Everything that is about *you* rather than about a
 * conversation lives here, so the main screens can stay almost entirely
 * conversation: there is no tab bar because none of this needs to be one tap
 * from a thread.
 */
export default function ProfileSheet({ onClose }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth0();
  const [view, setView] = useState('menu');     // menu | tools | acct-account | acct-org | acct-prefs
  const [look, setLook] = useState(storedMode());

  const go = (to) => { onClose(); navigate(to); };
  // Local for the first paint, the account for every other device: the same two places
  // the Settings screen writes, so the two controls can never disagree.
  const pick = (mode) => { applyMode(mode); setLook(mode); api.saveSettings({ theme: mode }).catch(() => {}); };

  if (view === 'tools') return <ToolsSheet onClose={onClose} back={() => setView('menu')} />;

  if (view.startsWith('acct-')) {
    return (
      <Sheet title="Account" tall onClose={onClose} back={() => setView('menu')}>
        <AccountSettings anchor={view} />
      </Sheet>
    );
  }

  return (
    <Sheet title="" label="You" onClose={onClose}>
      <div className="pf-me">
        <UserAvatar size={52} />
        <div>
          <strong>{user?.name || user?.nickname || 'You'}</strong>
          <span>{user?.email}</span>
        </div>
      </div>

      <div className="sx-group">
        <SheetRow icon="user" title="Account" hint="Who you are signed in as" onClick={() => setView('acct-account')} />
        <SheetRow icon="building" title="Organization" hint="Your workspace" onClick={() => setView('acct-org')} />
        <SheetRow icon="users" title="Org chart" hint="Who reports to whom" onClick={() => go('/org')} />
        <SheetRow icon="plug" title="Integrations" hint="Apps your agents can use" onClick={() => setView('tools')} />
        <SheetRow icon="sliders" title="Preferences" hint="Notifications and defaults" onClick={() => setView('acct-prefs')} />
        <SheetRow icon="card" title="Billing" hint="Balance, plan, and spend" onClick={() => go('/billing')} />
      </div>

      <div className="sx-group">
        <div className="sx-row sx-row--static">
          <span className="sx-icon"><i className="pf-swatch" aria-hidden="true" /></span>
          <span className="sx-text"><strong>Appearance</strong></span>
          <span className="pf-seg" role="radiogroup" aria-label="Appearance">
            {LOOKS.map(([key, label]) => (
              <button key={key} type="button" role="radio" aria-checked={look === key}
                      className={look === key ? 'on' : ''} onClick={() => pick(key)}>{label}</button>
            ))}
          </span>
        </div>
      </div>

      <div className="sx-group">
        <SheetRow icon="clock" title="Routines" onClick={() => go('/routines')} />
        <SheetRow icon="layers" title="Files and artifacts" onClick={() => go('/artifacts')} />
        <SheetRow icon="spark" title="Skills" onClick={() => go('/marketplace?tab=skills')} />
      </div>

      <div className="sx-group">
        <SheetRow icon="logout" title="Sign out" danger onClick={() => startLogout(logout)} />
      </div>
    </Sheet>
  );
}
