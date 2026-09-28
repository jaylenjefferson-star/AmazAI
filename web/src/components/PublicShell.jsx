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

// The primary items, listed once so the desktop bar and the mobile sheet stay
// in step. Product keeps its mega-menu on desktop; on mobile it flattens into
// the same disclosure list as everything else, so a phone never has to render
// a hover-only panel.
const PRIMARY_LINKS = [
  { to: '/use-cases', label: 'Solutions' },
  { to: '/faq', label: 'Resources' },
  { to: '/pricing', label: 'Pricing' },
];

export function TopNav() {
  const { loginWithRedirect } = useAuth0();
  // Only one mega-menu today, but modelling "which panel is open" as a key
  // rather than a boolean leaves room for Resources to grow into one without
  // another piece of state fighting it.
  const [open, setOpen] = useState(null);
  // The mobile sheet is separate state: it is a different affordance (a full
  // disclosure panel, not a popup menu) and closing one should not close the
  // other.
  const [mobileOpen, setMobileOpen] = useState(false);
  const navRef = useRef(null);
  const location = useLocation();

  // Close on navigation and on an outside click -- otherwise the panel
  // survives a route change and sits open over the new page.
  useEffect(() => { setOpen(null); setMobileOpen(false); }, [location.pathname]);
  useEffect(() => {
    function onClick(e) { if (navRef.current && !navRef.current.contains(e.target)) setOpen(null); }
    // Escape closes whichever surface is open and is the expected keyboard
    // affordance for a popup; focus stays on the trigger because we never
    // moved it.
    function onKey(e) { if (e.key === 'Escape') { setOpen(null); setMobileOpen(false); } }
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
            {/* A disclosure, not an application menu: the panel is plain
                navigation links, so we advertise aria-expanded/aria-controls
                only. role="menu"/"menuitem" would promise arrow-key menu
                navigation this widget doesn't implement; Tab/Enter reach the
                links, which is the behavior that actually exists. */}
            <button
              type="button"
              className="mkt-nav-item"
              aria-expanded={open === 'product'}
              aria-controls="mkt-product-menu"
              onClick={() => toggle('product')}
            >
              Product <span className="mkt-nav-caret" aria-hidden="true">▾</span>
            </button>
            {open === 'product' && (
              <div className="mkt-mega" id="mkt-product-menu" aria-label="Product">
                <div className="mkt-mega-panel">
                  {PRODUCT_MENU.map((group) => (
                    <div key={group.heading} className="mkt-mega-group">
                      <h3>{group.heading}</h3>
                      <ul>
                        {group.items.map((it) => (
                          <li key={it.label}><Link to={it.to}>{it.label}</Link></li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          {PRIMARY_LINKS.map((l) => (
            <Link key={l.label} className="mkt-nav-item" to={l.to}>{l.label}</Link>
          ))}
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

        {/* The hamburger only exists below the breakpoint where the primary
            row hides (see .mkt-nav-toggle in styles.css). It toggles a simple
            disclosure sheet rather than a second mega-menu, which is what a
            phone can actually operate. */}
        <button
          type="button"
          className="mkt-nav-toggle"
          aria-expanded={mobileOpen}
          aria-controls="mkt-mobile-menu"
          aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
          onClick={() => setMobileOpen((v) => !v)}
        >
          <span className="mkt-nav-toggle-bar" aria-hidden="true" />
          <span className="mkt-nav-toggle-bar" aria-hidden="true" />
          <span className="mkt-nav-toggle-bar" aria-hidden="true" />
        </button>
      </div>

      {mobileOpen && (
        <nav id="mkt-mobile-menu" className="mkt-mobile-menu" aria-label="Mobile">
          {PRODUCT_MENU.map((group) => (
            <div key={group.heading} className="mkt-mobile-group">
              <p className="mkt-mobile-heading">{group.heading}</p>
              {group.items.map((it) => (
                <Link key={it.label} to={it.to} className="mkt-mobile-link">{it.label}</Link>
              ))}
            </div>
          ))}
          <div className="mkt-mobile-group">
            <p className="mkt-mobile-heading">More</p>
            {PRIMARY_LINKS.map((l) => (
              <Link key={l.label} to={l.to} className="mkt-mobile-link">{l.label}</Link>
            ))}
          </div>
          <div className="mkt-mobile-cta">
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
        </nav>
      )}
    </header>
  );
}

/**
 * The footer.
 *
 * Six restrained groups — Product, Solutions, Resources, Developers, Company,
 * Legal — that act as the site's sitemap. Every link points at a route that
 * already exists (marketing pages, homepage anchors, or legal documents); no
 * new routes are invented here. The Developers group has no dedicated route
 * yet, so it points at the closest existing pages (Integrations, the API-flavoured
 * How it works) rather than fabricating links.
 *
 * Kept visually quiet on purpose: it follows the final CTA and must not
 * compete with it, so it is plain columns of text links, not a second hero.
 */
const HOME_ANCHOR = '/welcome-to-amazai';
const FOOTER_GROUPS = [
  {
    heading: 'Product',
    links: [
      { to: `${HOME_ANCHOR}#companions`, label: 'Companions' },
      { to: `${HOME_ANCHOR}#rooms`, label: 'Rooms' },
      { to: `${HOME_ANCHOR}#routines`, label: 'Routines' },
      { to: '/product', label: 'Overview' },
      { to: '/pricing', label: 'Pricing' },
    ],
  },
  {
    heading: 'Solutions',
    links: [
      { to: '/use-cases', label: 'Use cases' },
      { to: '/for-teams', label: 'For teams' },
      { to: '/enterprise', label: 'Enterprise' },
    ],
  },
  {
    heading: 'Resources',
    links: [
      { to: '/how-it-works', label: 'How it works' },
      { to: '/integrations', label: 'Integrations' },
      { to: '/faq', label: 'FAQ' },
    ],
  },
  {
    heading: 'Developers',
    links: [
      { to: '/integrations', label: 'Connectors' },
      { to: `${HOME_ANCHOR}#browser-computer`, label: 'Browser & Computer' },
    ],
  },
  {
    heading: 'Company',
    links: [
      { to: '/about', label: 'About' },
      { to: '/contact', label: 'Contact' },
      { to: '/security', label: 'Security' },
    ],
  },
  {
    heading: 'Legal',
    links: [
      { to: '/legal', label: 'Legal & Trust' },
      { to: '/terms', label: 'Terms' },
      { to: '/privacy', label: 'Privacy' },
      { to: '/cookie-policy', label: 'Cookies' },
      { to: '/acceptable-use', label: 'Acceptable use' },
      { to: '/security-disclosure', label: 'Security disclosure' },
    ],
  },
];

export function PublicFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="public-foot">
      <div className="public-foot-top">
        <div className="public-foot-brand">
          <Logo size={22} title="AmazAI" />
          <p>A customizable AI team that gets work done — with you in control.</p>
        </div>

        <div className="public-foot-groups">
          {FOOTER_GROUPS.map((group) => (
            <div key={group.heading} className="public-foot-col">
              <h3>{group.heading}</h3>
              {group.links.map((l) => (
                <Link key={l.label} to={l.to}>{l.label}</Link>
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="public-foot-bottom">
        <span>© {year} AmazFlow, LLC. All rights reserved.</span>
      </div>
    </footer>
  );
}
