import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import Logo from './Logo';
import { startLogin, useAuth0 } from '../auth0';

/**
 * Chrome shared by every logged-out page: the marketing pages, the Resources
 * pages, and the legal documents.
 *
 * The top bar stays to four primary items — Product, Solutions, Resources,
 * Pricing — because a marketing nav that lists every page it owns stops being
 * a nav and becomes a sitemap. Product opens a structured mega-menu; the rest
 * of the surface (Use Cases, For Teams, Enterprise, Legal & Trust, Contact)
 * is one click away through those items or the footer, which is where a
 * sitemap belongs.
 */
export default function PublicShell({ children, wide }) {
  return (
    <div className="public">
      <TopNav />
      <main className={wide ? 'public-main wide' : 'public-main'}>{children}</main>
      <PublicFooter />
    </div>
  );
}

/**
 * The Product mega-menu.
 *
 * Grouped WORK / CONNECT / DISCOVER, matching the redesign spec. Links point
 * at on-page homepage anchors (the homepage sections land in FEAT-002/003) or
 * an existing route where one fits, so no new routes are invented here. A
 * visitor who follows an anchor before its section exists lands on the
 * homepage, which is the right fallback while the page is built out.
 */
const HOME = '/welcome-to-amazai';
const PRODUCT_MENU = [
  {
    heading: 'Work',
    items: [
      { to: `${HOME}#companions`, label: 'Companions' },
      { to: `${HOME}#rooms`, label: 'Rooms' },
      { to: `${HOME}#routines`, label: 'Routines' },
      { to: `${HOME}#artifacts`, label: 'Artifacts' },
    ],
  },
  {
    heading: 'Connect',
    items: [
      { to: '/integrations', label: 'Connectors' },
      { to: `${HOME}#browser-computer`, label: 'Browser & Computer' },
      { to: `${HOME}#developer-tools`, label: 'Developer Tools' },
    ],
  },
  {
    heading: 'Discover',
    items: [
      { to: `${HOME}#unified-inbox`, label: 'Unified Inbox' },
      { to: `${HOME}#search`, label: 'Search' },
      { to: `${HOME}#templates`, label: 'Templates' },
    ],
  },
];

export function TopNav() {
  const { loginWithRedirect } = useAuth0();
  // Only one mega-menu today, but modelling "which panel is open" as a key
  // rather than a boolean leaves room for Resources to grow into one without
  // another piece of state fighting it.
  const [open, setOpen] = useState(null);
  const navRef = useRef(null);
  const location = useLocation();

  // Close on navigation and on an outside click -- otherwise the panel
  // survives a route change and sits open over the new page.
  useEffect(() => { setOpen(null); }, [location.pathname]);
  useEffect(() => {
    function onClick(e) { if (navRef.current && !navRef.current.contains(e.target)) setOpen(null); }
    // Escape closes the menu and is the expected keyboard affordance for a
    // popup; focus stays on the trigger because we never moved it.
    function onKey(e) { if (e.key === 'Escape') setOpen(null); }
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  const toggle = (key) => setOpen((cur) => (cur === key ? null : key));

  return (
    <header className="mkt-nav">
      <div className="mkt-nav-inner" ref={navRef}>
        <Link to={HOME} className="mkt-nav-brand" aria-label="AmazAI home">
          <Logo size={24} title="AmazAI" />
        </Link>

        <nav className="mkt-nav-primary" aria-label="Primary">
          <div className="mkt-mega-wrap">
            <button
              type="button"
              className="mkt-nav-item"
              aria-expanded={open === 'product'}
              aria-haspopup="true"
              onClick={() => toggle('product')}
            >
              Product <span className="mkt-nav-caret" aria-hidden="true">▾</span>
            </button>
            {open === 'product' && (
              <div className="mkt-mega" role="menu" aria-label="Product">
                <div className="mkt-mega-panel">
                  {PRODUCT_MENU.map((group) => (
                    <div key={group.heading} className="mkt-mega-group">
                      <h3>{group.heading}</h3>
                      {group.items.map((it) => (
                        <Link key={it.label} to={it.to} role="menuitem">{it.label}</Link>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          <Link className="mkt-nav-item" to="/use-cases">Solutions</Link>
          <Link className="mkt-nav-item" to="/faq">Resources</Link>
          <Link className="mkt-nav-item" to="/pricing">Pricing</Link>
        </nav>

        <div className="mkt-nav-cta">
          <button
            type="button"
            className="mkt-nav-signin"
            onClick={() => startLogin(loginWithRedirect, { returnTo: '/' })}
          >
            Sign in
          </button>
          <button
            type="button"
            className="mkt-nav-start"
            onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' })}
          >
            Start free
          </button>
        </div>
      </div>
    </header>
  );
}

export function PublicFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="public-foot">
      <div className="public-foot-top">
        <div className="public-foot-brand">
          <Logo size={22} title="AmazAI" />
          <p>A customizable AI team that gets work done — with you in control.</p>
        </div>

        <div className="public-foot-col">
          <h3>Product</h3>
          <Link to="/how-it-works">How it works</Link>
          <Link to="/product">Agents &amp; Skills</Link>
          <Link to="/pricing">Pricing</Link>
          <Link to="/integrations">Integrations</Link>
          <Link to="/security">Security</Link>
        </div>

        <div className="public-foot-col">
          <h3>Resources</h3>
          <Link to="/use-cases">Use Cases</Link>
          <Link to="/faq">FAQ</Link>
          <Link to="/for-teams">For Teams</Link>
          <Link to="/enterprise">Enterprise</Link>
          <Link to="/contact">Contact / Demo</Link>
        </div>

        <div className="public-foot-col">
          <h3>Company</h3>
          <Link to="/about">About us</Link>
          <Link to="/legal">Legal &amp; Trust</Link>
          <a href="mailto:hello@amazai.co">hello@amazai.co</a>
        </div>
      </div>

      <div className="public-foot-bottom">
        <span>© {year} AmazFlow, LLC. All rights reserved.</span>
      </div>
    </footer>
  );
}
