import { Link } from 'react-router-dom';
import { startLogin, useAuth0 } from '../../auth0';
import ProductFrame from './ProductFrame';

/**
 * The editorial hero. Type carries the identity — a large clamp()-scaled
 * headline and one tight supporting paragraph — and the real product
 * composition (ProductFrame), not an abstract AI illustration, is the primary
 * visual. Copy is verbatim from the spec.
 */
export default function Hero() {
  const { loginWithRedirect } = useAuth0();
  return (
    <section className="mkt-hero">
      <div className="mkt-container mkt-hero-inner">
        <div className="mkt-hero-copy">
          <h1 className="mkt-hero-title">Your work has a new operating system.</h1>
          <p className="mkt-hero-lede">
            Build a team of AI companions that can research, create, use your
            tools, run recurring work, and collaborate with you from one
            workspace.
          </p>
          <div className="mkt-hero-cta">
            <button className="mkt-btn mkt-btn-primary"
                    onClick={() => startLogin(loginWithRedirect, { signup: true, returnTo: '/welcome' })}>
              Start for free
            </button>
            <Link className="mkt-btn mkt-btn-ghost" to="/how-it-works">
              See how it works
            </Link>
          </div>
          <p className="mkt-hero-fine">No credit card required · Set up in minutes</p>
        </div>

        <div className="mkt-hero-art">
          <ProductFrame />
        </div>
      </div>
    </section>
  );
}
