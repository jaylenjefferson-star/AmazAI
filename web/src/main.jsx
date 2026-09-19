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
import Onboarding, { hasOnboarded } from './screens/Onboarding';
import Home from './screens/Home';
import { Agents, Artifacts, Rooms, Routines } from './screens/Sections';
import Settings from './screens/Settings';
import Task from './screens/Task';
import Usage from './screens/Usage';
import Gallery from './screens/Gallery';

/**
 * Routing.
 *
 * Three tiers, and the boundary between them is the point:
 *
 *   public    — the landing page, and the character gallery
 *   gated     — everything behind AuthGate
 *   first-run — gated, but redirected to onboarding until it is done
 *
 * The gallery is public on purpose: it is a design surface with no data on
 * it, and needing to sign in to check whether an animation reads correctly
 * would mean checking it less often.
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
      <Route path="/characters" element={<Gallery />} />

      {/* First run */}
      <Route path="/welcome" element={<Protected><Onboarding /></Protected>} />

      {/* The application */}
      <Route element={<Protected><FirstRunGuard><Shell /></FirstRunGuard></Protected>}>
        <Route path="/" element={<Home />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/new" element={<Agents />} />
        <Route path="/agents/:agentId" element={<Task />} />
        <Route path="/rooms" element={<Rooms />} />
        <Route path="/routines" element={<Routines />} />
        <Route path="/artifacts" element={<Artifacts />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/usage" element={<Usage />} />
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

  const isPublicPath = location.pathname === '/characters'
    || location.pathname === '/welcome-to-amazai';

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
