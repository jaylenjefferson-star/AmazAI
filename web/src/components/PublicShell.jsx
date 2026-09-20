import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import Logo from './Logo';
import { startLogin, useAuth0 } from '../auth0';

/**
 * Chrome shared by every logged-out page: Home, How it works, Pricing,
 * Product, Security, the Resources pages, and the legal documents.
 *
 * The top bar deliberately stays to five items — Product, How it works,
 * Pricing, Security, Resources — because a marketing nav that lists every
 * page it owns stops being a nav and becomes a sitemap. Everything else
 * (Use Cases, For Teams, Enterprise, Legal & Trust, Contact) is one click
 * away through the Resources menu or the footer, which is where a sitemap
 * belongs.
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

const RESOURCES = [
  { to: '/faq', label: 'FAQ' },
  { to: '/integrations', label: 'Integrations' },
  { to: '/use-cases', label: 'Use Cases' },
  { to: '/contact', label: 'Contact' },
];

export function TopNav() {
  const { loginWithRedirect } = useAuth0();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const location = useLocation();

  // Close the Resources dropdown on navigation and on an outside click —
  // otherwise it survives a route change and sits open over the new page.
  useEffect(() => { setOpen(false); }, [location.pathname]);
  useEffect(() => {
    function onClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  return (
    <header className="public-nav">
      <Link to="/welcome-to-amazai" className="public-nav-brand">
        <Logo size={24} title="AmazAI" />
      </Link>
      <nav className="public-nav-links">
        <Link to="/product">Product</Link>
        <Link to="/how-it-works">How it works</Link>
        <Link to="/pricing">Pricing</Link>
        <Link to="/security">Security</Link>
        <div className="public-nav-dropdown" ref={ref}>
          <button
            type="button"
            className="public-nav-dropdown-trigger"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            Resources <span aria-hidden="true">▾</span>
          </button>
          {open && (
            <div className="public-nav-dropdown-menu">
              {RESOURCES.map((r) => (
                <Link key={r.to} to={r.to}>{r.label}</Link>
              ))}
            </div>
          )}
        </div>
      </nav>
      <span style={{ flex: 1 }} />
      <button className="ghost" onClick={() => startLogin(loginWithRedirect, { returnTo: '/' })}>
        Sign in
      </button>
      <button className="primary" onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' })}>
        Create your AmazAI
      </button>
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
