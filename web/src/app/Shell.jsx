import { NavLink, Outlet, useLocation } from 'react-router-dom';
import AccountMenu from '../components/AccountMenu';
import Logo from '../components/Logo';

/**
 * The application shell.
 *
 * Mobile-first on purpose: a three-panel desktop layout stretched onto a
 * phone is how most consoles become unusable on the device they are most
 * often opened on.
 *
 * What a phone gets is the inbox and the conversation it leads to. The rail
 * used to carry seven destinations, three of which -- Home, Agents and Rooms
 * -- are now the same list, and the rest are things you go to from a
 * conversation rather than instead of one. So `primary` marks what a phone
 * shows; a desktop, which has the room, still shows every destination.
 *
 * The vocabulary is AmazAI's own: a Room is several companions on one
 * thread, a Routine is work that runs on a schedule, and an Artifact is
 * something a run produced and sealed.
 */
const NAV = [
  { to: '/',           label: 'Inbox',      end: true, glyph: '◉', primary: true },
  { to: '/connectors', label: 'Connectors', glyph: '⌁', primary: true },
  { to: '/routines',   label: 'Routines',   glyph: '◐' },
  { to: '/artifacts',  label: 'Artifacts',  glyph: '▤' },
  { to: '/settings',   label: 'Settings',   glyph: '⚙', primary: true },
];

/**
 * A conversation owns the screen on a phone.
 *
 * Reading a thread and answering in it is the whole task, and a bottom bar
 * over a composer costs a thumb's width of it for destinations nobody wants
 * mid-sentence. The rail is still one back-gesture away, and it never leaves
 * on a desktop.
 */
const FOCUSED = [/^\/agents\/[^/]+$/, /^\/rooms\/[^/]+$/];

export default function Shell() {
  const { pathname } = useLocation();
  const focused = FOCUSED.some((re) => re.test(pathname));

  return (
    <div className="shell" data-focused={focused ? 'true' : undefined}>
      <header className="shell-top">
        <Logo size={24} title="AmazAI" />
        <span style={{ flex: 1 }} />
        <AccountMenu />
      </header>

      <nav className="shell-rail" aria-label="Sections">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}
                   data-primary={n.primary ? 'true' : undefined}
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
