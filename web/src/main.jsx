import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  BrowserRouter, Navigate, Route, Routes, useLocation,
} from 'react-router-dom';

import './styles.css';
import './characters/characters.css';

import { AmazAIAuthProvider, configured, useAuth0 } from './auth0';
import AuthGate from './components/AuthGate';
import Shell from './app/Shell';

import Landing from './screens/Landing';
import About from './screens/About';
import PolicyPage from './screens/PolicyPage';
import termsRaw from './content/terms-of-use.md?raw';
import privacyRaw from './content/privacy-policy.md?raw';
import securityRaw from './content/security-responsible-disclosure.md?raw';
import cookieRaw from './content/cookie-policy.md?raw';
import acceptableUseRaw from './content/acceptable-use-policy.md?raw';
import Onboarding, { hasOnboarded } from './screens/Onboarding';
import Home from './screens/Home';
import { Agents, Artifacts, Rooms, Routines } from './screens/Sections';
import Settings from './screens/Settings';
import Task from './screens/Task';
import Room from './screens/Room';
import Usage from './screens/Usage';
import Gallery from './screens/Gallery';
import Connectors from './screens/Connectors';

const PUBLIC_PATHS = ['/welcome-to-amazai', '/about', '/terms', '/privacy', '/cookie-policy', '/acceptable-use', '/security'];

/**
 * Routing.
 *
 * Three tiers, and the boundary between them is the point:
 *
 *   public    — the marketing pages: landing, about, and the legal documents
 *   gated     — everything behind AuthGate
 *   first-run — gated, but redirected to onboarding until it is done
 *
 * The public tier is reachable whether or not a visitor is signed in — a
 * signed-in owner should be able to open the Privacy Policy from the footer
 * without being bounced out of their session.
 */

function Protected({ children }) {
  return <AuthGate>{children}</AuthGate>;
}

/** Send a signed-in visitor who has never set up to onboarding first. */
function FirstRunGuard({ children }) {
  const location = useLocation();
  if (!hasOnboarded() && location.pathname !== '/welcome') {
    return <Navigate to="/welcome" replace />;
  }
  return children;
}

function PublicOnly({ children }) {
  const { isAuthenticated, isLoading } = useAuth0();
  if (isLoading) return null;
  if (isAuthenticated) return <Navigate to="/" replace />;
  return children;
}

function Router() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/welcome-to-amazai" element={<PublicOnly><Landing /></PublicOnly>} />
      <Route path="/about" element={<About />} />
      <Route path="/terms" element={<PolicyPage raw={termsRaw} />} />
      <Route path="/privacy" element={<PolicyPage raw={privacyRaw} />} />
      <Route path="/cookie-policy" element={<PolicyPage raw={cookieRaw} />} />
      <Route path="/acceptable-use" element={<PolicyPage raw={acceptableUseRaw} />} />
      <Route path="/security" element={<PolicyPage raw={securityRaw} />} />

      {/* First run */}
      <Route path="/welcome" element={<Protected><Onboarding /></Protected>} />

      {/* The application */}
      <Route element={<Protected><FirstRunGuard><Shell /></FirstRunGuard></Protected>}>
        <Route path="/" element={<Home />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/new" element={<Agents />} />
        <Route path="/agents/:agentId" element={<Task />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/rooms" element={<Rooms />} />
        <Route path="/rooms/:roomId" element={<Room />} />
        <Route path="/routines" element={<Routines />} />
        <Route path="/artifacts" element={<Artifacts />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/characters" element={<Gallery />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** Signed-out visitors land on the marketing page rather than a bare gate. */
function Entry() {
  const { isAuthenticated, isLoading } = useAuth0();
  const location = useLocation();

  if (!configured) return <Router />;
  if (isLoading) return null;

  const isPublicPath = PUBLIC_PATHS.includes(location.pathname);

  if (!isAuthenticated && !isPublicPath) return <Landing />;
  return <Router />;
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <AmazAIAuthProvider>
        <Entry />
      </AmazAIAuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
