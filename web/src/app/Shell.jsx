import { NavLink, Outlet } from 'react-router-dom';
import AccountMenu from '../components/AccountMenu';
import Logo from '../components/Logo';

/**
 * The application shell.
 *
 * Mobile-first on purpose: a bottom bar on a phone, a rail on a desktop, and
 * the same six destinations either way. A three-panel desktop layout stretched
 * onto a phone is how most consoles become unusable on the device they are
 * most often opened on.
 *
 * Six destinations, and the vocabulary is AmazAI's own: a Room is several
 * companions on one thread, a Routine is work that runs on a schedule, and an
 * Artifact is something a run produced and sealed.
 */
const NAV = [
  { to: '/',          label: 'Home',      end: true, glyph: '◉' },
  { to: '/agents',    label: 'Agents',    glyph: '◍' },
  { to: '/connectors',label: 'Connectors',glyph: '⌁' },
  { to: '/rooms',     label: 'Rooms',     glyph: '◎' },
  { to: '/routines',  label: 'Routines',  glyph: '◐' },
  { to: '/artifacts', label: 'Artifacts', glyph: '▤' },
  { to: '/settings',  label: 'Settings',  glyph: '⚙' },
];

export default function Shell() {
  return (
    <div className="shell">
      <header className="shell-top">
        <Logo size={24} title="AmazAI" />
        <span style={{ flex: 1 }} />
        <AccountMenu />
      </header>

      <nav className="shell-rail" aria-label="Sections">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}
                   className={({ isActive }) => `rail-item ${isActive ? 'on' : ''}`}>
            <span className="rail-glyph" aria-hidden="true">{n.glyph}</span>
            <span className="rail-label">{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <main className="shell-main">
        <Outlet />
      </main>
    </div>
  );
}
