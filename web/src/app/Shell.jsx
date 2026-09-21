import { useEffect } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { applyMode, explicitMode } from '../theme';
import { ContactsProvider } from '../components/ContactCard';
import ConnectionBar from '../components/ConnectionBar';
import Icon from '../components/Icon';
import PresenceFeed from '../components/PresenceFeed';
import Inbox from '../screens/Inbox';
import { useMediaQuery } from '../hooks/useMediaQuery';

/**
 * The application shell: almost nothing, on purpose.
 *
 * AmazAI is a communication app whose contacts happen to be AI teammates, so the
 * conversation gets the whole screen. There is no tab bar, no logo bar, no rail:
 * everything that is not a conversation is reached from a conversation --
 * the avatar (you), the + (start something), a teammate's profile (its tools and
 * permissions) -- and comes back the same way.
 *
 * On a phone that is the roster and the thread it opens, one at a time. On a
 * desktop it is the same thing widened: the roster on the left, the open
 * conversation beside it, and (from the conversation) a details pane on the
 * right. One mental model at every size, not a separate dashboard.
 */

/** A conversation owns the screen: an agent's thread, or a room's. `/agents/new`
 *  is a full-screen flow of its own, not a conversation. */
const FOCUSED = [/^\/agents\/(?!new$)[^/]+$/, /^\/rooms\/[^/]+$/];

/** Screens that own their own chrome, so the page frame stays out of the way. */
const OWN_CHROME = [/^\/agents\/new$/];

const TITLES = [
  [/^\/agents\/[^/]+\/settings$/, 'Settings'],
  [/^\/agents/, 'Agents'],
  [/^\/marketplace/, 'Skills'],
  [/^\/connectors/, 'Connect a tool'],
  [/^\/rooms/, 'Rooms'],
  [/^\/org/, 'Org chart'],
  [/^\/routines/, 'Routines'],
  [/^\/artifacts/, 'Files and artifacts'],
  [/^\/settings/, 'Settings'],
  [/^\/usage/, 'Billing'],
  [/^\/characters/, 'Characters'],
];

/** The detail pane before a conversation is chosen. Not a route: picking a row
 *  does not navigate away from something, it fills a pane beside it. */
function NothingOpen() {
  return (
    <div className="split-empty">
      <Icon name="inbox" size={28} />
      <p>Choose an agent or a room to open the conversation here.</p>
    </div>
  );
}

/** Everything that is not a conversation: a floating back control and a quiet
 *  title, and the page underneath. */
function PageFrame({ pathname, children }) {
  const navigate = useNavigate();
  const title = (TITLES.find(([re]) => re.test(pathname)) || [null, ''])[1];
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate('/'));
  return (
    <div className="pgf">
      <header className="pgf-bar">
        <button type="button" className="pgf-back" aria-label="Back" onClick={back}>
          <Icon name="back" size={21} />
        </button>
        <h1>{title}</h1>
        <span className="pgf-spacer" />
      </header>
      <div className="pgf-body">{children}</div>
    </div>
  );
}

export default function Shell() {
  const { pathname } = useLocation();
  const focused = FOCUSED.some((re) => re.test(pathname));
  const ownChrome = OWN_CHROME.some((re) => re.test(pathname));
  const home = pathname === '/';
  // One breakpoint decides everything: below it a phone has room for a single
  // screen and the roster and the conversation trade places on their own.
  const desktop = useMediaQuery('(min-width: 900px)');
  const split = desktop && (home || focused);

  // A theme chosen on another device: adopted here only if this device has never chosen,
  // so it can fill a gap but never override what someone picked on this screen.
  useEffect(() => {
    if (explicitMode()) return;
    api.settings().then((s) => { if (s?.theme && s.theme !== 'dark') applyMode(s.theme); }).catch(() => {});
  }, []);

  return (
    <ContactsProvider>
    <div className="app" data-focused={focused ? 'true' : undefined}>
      <PresenceFeed />
      <ConnectionBar />
      <main className={`app-main${split ? ' app-main--split' : ''}`}>
        {split ? (
          <div className="app-split">
            <aside className="app-list"><Inbox variant="pane" /></aside>
            {/* Not `<Outlet/>` at "/": that route's own element is this same
                `<Inbox/>`, and mounting it twice would fetch the threads and
                approvals twice for two lists that would then drift apart. */}
            <section className="app-detail">{focused ? <Outlet /> : <NothingOpen />}</section>
          </div>
        ) : (home || focused || ownChrome) ? (
          <Outlet />
        ) : (
          <PageFrame pathname={pathname}><Outlet /></PageFrame>
        )}
      </main>
    </div>
    </ContactsProvider>
  );
}
