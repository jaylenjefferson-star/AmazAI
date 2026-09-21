import { NavLink, Outlet } from 'react-router-dom';

/**
 * The admin surface: a separate interface, on purpose.
 *
 * WHY this is a sibling route group and not a second build target.
 * The blueprint (docs/architecture/02-control-plane-ia.md) wants governance
 * on its own surface with NO bot chat composer -- the member app is where you
 * talk to Bots; the admin app is where you govern them, and mixing the two
 * invites someone to fire a destructive control from muscle memory in a chat
 * box. The member app already lives inside <Shell>, which owns the Composer.
 * This AdminShell is rendered OUTSIDE <Shell> from web/src/main.jsx, so it
 * imports nothing from the chat surface: no Composer, no thread view, no
 * roster. A second Vite build would duplicate the auth0/api plumbing for no
 * isolation benefit at single-tenant scale; true origin-isolation (a separate
 * deployment) is a documented v1.5 follow-up. A distinct route with its own
 * shell and no composer satisfies the "entirely different interface" intent
 * today while reusing the exact same authenticated session.
 *
 * This component MUST NOT import or render the Composer or any bot-chat
 * component. The only way back to the conversational app is the explicit
 * "Open in member app" link-out below.
 */
export default function AdminShell() {
  return (
    <div className="admin">
      <header className="admin-bar">
        <div className="admin-brand">
          <strong>AmazAI Admin</strong>
          <span className="admin-scope">Governance console</span>
        </div>
        <nav className="admin-nav">
          {/* `end` on the index so it does not stay lit under the others. */}
          <NavLink to="/admin" end className={({ isActive }) => (isActive ? 'active' : '')}>Directory</NavLink>
          <NavLink to="/admin/killswitch" className={({ isActive }) => (isActive ? 'active' : '')}>Kill switch</NavLink>
          <NavLink to="/admin/audit" className={({ isActive }) => (isActive ? 'active' : '')}>Audit log</NavLink>
        </nav>
        {/* The link-out, not a composer. This is the only door back to the
            member app, and it is a plain navigation -- the admin surface never
            renders a conversation of its own. */}
        <a className="admin-exit" href="/">Open in member app →</a>
      </header>

      {/* Stronger-auth framing: this surface governs everyone. A note, not a
          gate -- the real gate is server-side RBAC on /admin/*; this reminds
          whoever is here what the actions below actually reach. */}
      <p className="admin-note">
        Admin actions here apply across the organization and are recorded in the
        append-only audit log. Destructive and governance actions require a typed
        confirmation and a reason before they run.
      </p>

      <main className="admin-body">
        <Outlet />
      </main>
    </div>
  );
}
