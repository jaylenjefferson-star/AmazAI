import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { startLogout, useAuth0 } from '../auth0';

/** Avatar, identity, settings, sign out. */
export default function AccountMenu() {
  const { user, logout } = useAuth0();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const name = user?.name || user?.nickname || 'Signed in';
  const email = user?.email || user?.sub;
  const initial = (user?.name || user?.email || '?').trim().charAt(0).toUpperCase();

  return (
    <div className="account" ref={ref}>
      <button className="account-trigger" onClick={() => setOpen((o) => !o)}
              aria-haspopup="menu" aria-expanded={open} aria-label="Account">
        {user?.picture
          ? <img src={user.picture} alt="" width="26" height="26" />
          : <span className="account-initial">{initial}</span>}
      </button>

      {open && (
        <div className="account-menu" role="menu">
          <div className="account-who">
            {user?.picture
              ? <img src={user.picture} alt="" width="36" height="36" />
              : <span className="account-initial lg">{initial}</span>}
            <div>
              <strong>{name}</strong>
              <span>{email}</span>
            </div>
          </div>
          <Link className="account-item" to="/settings" role="menuitem"
                onClick={() => setOpen(false)}>Settings</Link>
          <Link className="account-item" to="/usage" role="menuitem"
                onClick={() => setOpen(false)}>Usage</Link>
          <button className="account-item danger" role="menuitem"
                  onClick={() => startLogout(logout)}>Sign out</button>
        </div>
      )}
    </div>
  );
}
