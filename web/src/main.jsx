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
import Inbox from './screens/Inbox';
import { Agents, Artifacts, Rooms, Routines } from './screens/Sections';
import Settings from './screens/Settings';
import Task from './screens/Task';
import CompanionSettings from './screens/CompanionSettings';
import Room from './screens/Room';
import Usage from './screens/Usage';
import Gallery from './screens/Gallery';
import Connectors from './screens/Connectors';
import HowItWorks from './screens/marketing/HowItWorks';
import Product from './screens/marketing/Product';
import Pricing from './screens/marketing/Pricing';
import Integrations from './screens/marketing/Integrations';
import UseCases from './screens/marketing/UseCases';
import SecurityOverview from './screens/marketing/SecurityOverview';
import ForTeams from './screens/marketing/ForTeams';
import Enterprise from './screens/marketing/Enterprise';
import FAQ from './screens/marketing/FAQ';
import Contact from './screens/marketing/Contact';
import Legal from './screens/marketing/Legal';

const PUBLIC_PATHS = [
  '/welcome-to-amazai', '/about', '/terms', '/privacy', '/cookie-policy',
  '/acceptable-use', '/security', '/security-disclosure', '/how-it-works',
  '/product', '/pricing', '/integrations', '/use-cases', '/for-teams',
  '/enterprise', '/faq', '/contact', '/legal',
];

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
      <Route path="/how-it-works" element={<HowItWorks />} />
      <Route path="/product" element={<Product />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/integrations" element={<Integrations />} />
      <Route path="/use-cases" element={<UseCases />} />
      <Route path="/security" element={<SecurityOverview />} />
      <Route path="/for-teams" element={<ForTeams />} />
      <Route path="/enterprise" element={<Enterprise />} />
      <Route path="/faq" element={<FAQ />} />
      <Route path="/contact" element={<Contact />} />
      <Route path="/legal" element={<Legal />} />
      <Route path="/terms" element={<PolicyPage raw={termsRaw} />} />
      <Route path="/privacy" element={<PolicyPage raw={privacyRaw} />} />
      <Route path="/cookie-policy" element={<PolicyPage raw={cookieRaw} />} />
      <Route path="/acceptable-use" element={<PolicyPage raw={acceptableUseRaw} />} />
      <Route path="/security-disclosure" element={<PolicyPage raw={securityRaw} />} />
      <Route path="/security-responsible-disclosure" element={<Navigate to="/security-disclosure" replace />} />

      {/* First run */}
      <Route path="/welcome" element={<Protected><Onboarding /></Protected>} />

      {/* The application */}
      <Route element={<Protected><FirstRunGuard><Shell /></FirstRunGuard></Protected>}>
        <Route path="/" element={<Inbox />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/new" element={<Agents />} />
        <Route path="/agents/:agentId" element={<Task />} />
        <Route path="/agents/:agentId/settings" element={<CompanionSettings />} />
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
