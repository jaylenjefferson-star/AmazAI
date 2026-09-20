import { NavLink, Outlet, useLocation } from 'react-router-dom';
import AccountMenu from '../components/AccountMenu';
import Icon from '../components/Icon';
import Logo from '../components/Logo';
import PresenceFeed from '../components/PresenceFeed';
import Inbox from '../screens/Inbox';
import { useMediaQuery } from '../hooks/useMediaQuery';

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
  { to: '/',           label: 'Inbox',      end: true, icon: 'inbox',   primary: true },
  { to: '/marketplace', label: 'Marketplace', icon: 'store', primary: true },
  { to: '/routines',   label: 'Routines',   icon: 'clock' },
  { to: '/artifacts',  label: 'Artifacts',  icon: 'layers' },
  { to: '/settings',   label: 'Settings',   icon: 'sliders', primary: true },
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

/** The detail pane before a conversation is chosen. Not a route -- picking a
 *  row does not navigate away from something, it fills a pane beside it. */
function NothingOpen() {
  return (
    <div className="split-empty">
      <Icon name="inbox" size={28} />
      <p>Choose a companion or a room to open the conversation here.</p>
    </div>
  );
}

export default function Shell() {
  const { pathname } = useLocation();
  const focused = FOCUSED.some((re) => re.test(pathname));
  // ≥900px is also where the rail stops being a bottom bar (styles.css), so
  // a single breakpoint decides both: below it, a phone has room for one
  // screen at a time and the inbox route and the conversation route already
  // trade places on their own.
  const desktop = useMediaQuery('(min-width: 900px)');

  /**
   * At desktop width, the inbox is a list beside whatever it leads to, not a
   * screen you leave to open one. `/routines`, `/settings` and the rest are
   * still full width -- the split is specific to conversations, which is
   * the thing this was written to fix: today desktop keeps the rail and
   * navigates between them, so opening a companion loses the list it came
   * from and there is no way back to it without a second click.
   */
  const split = desktop && (pathname === '/' || focused);

  return (
    <div className="shell" data-focused={focused ? 'true' : undefined}>
      <PresenceFeed />
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
            <Icon name={n.icon} size={21} className="rail-glyph" />
            <span className="rail-label">{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <main className={`shell-main${split ? ' shell-main--split' : ''}`}>
        {split ? (
          <div className="shell-split">
            <div className="shell-split-list"><Inbox variant="pane" /></div>
            {/* Not `<Outlet/>` at "/": that route's own element is this same
                `<Inbox/>`, and mounting it twice would fetch the account's
                threads and approvals twice for two lists that would then
                drift the moment one of them re-read. */}
            <div className="shell-split-detail">{focused ? <Outlet /> : <NothingOpen />}</div>
          </div>
        ) : (
          <Outlet />
        )}
      </main>
    </div>
  );
}
