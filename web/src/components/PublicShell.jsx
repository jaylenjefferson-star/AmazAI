import { Link } from 'react-router-dom';
import Logo from './Logo';
import { startLogin, useAuth0 } from '../auth0';

/**
 * Chrome shared by every logged-out page: the marketing home, About, and the
 * five legal documents. A visitor reading the Privacy Policy should not land
 * on a bare document with no way back to the product — the same header and
 * footer that sell AmazAI also frame the pages that explain how it handles
 * their data.
 */
export default function PublicShell({ children, wide }) {
  const { loginWithRedirect } = useAuth0();

  return (
    <div className="public">
      <header className="public-nav">
        <Link to="/" className="public-nav-brand">
          <Logo size={24} title="AmazAI" />
        </Link>
        <nav className="public-nav-links">
          <Link to="/about">About</Link>
          <Link to="/security">Security</Link>
        </nav>
        <span style={{ flex: 1 }} />
        <button className="ghost" onClick={() => startLogin(loginWithRedirect, { returnTo: '/' })}>
          Sign in
        </button>
        <button className="primary" onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' })}>
          Create your AmazAI
        </button>
      </header>

      <main className={wide ? 'public-main wide' : 'public-main'}>{children}</main>

      <PublicFooter />
    </div>
  );
}

export function PublicFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="public-foot">
      <div className="public-foot-top">
        <div className="public-foot-brand">
          <Logo size={22} title="AmazAI" />
          <p>A private operator console for a small cast of AI companions.</p>
        </div>

        <div className="public-foot-col">
          <h3>Company</h3>
          <Link to="/about">About us</Link>
          <a href="mailto:hello@amazai.co">Contact</a>
        </div>

        <div className="public-foot-col">
          <h3>Legal</h3>
          <Link to="/terms">Terms of Use</Link>
          <Link to="/privacy">Privacy Policy</Link>
          <Link to="/cookie-policy">Cookie Policy</Link>
          <Link to="/acceptable-use">Acceptable Use Policy</Link>
        </div>

        <div className="public-foot-col">
          <h3>Trust</h3>
          <Link to="/security">Security &amp; Disclosure</Link>
        </div>
      </div>

      <div className="public-foot-bottom">
        <span>© {year} AmazFlow, LLC. All rights reserved.</span>
      </div>
    </footer>
  );
}
