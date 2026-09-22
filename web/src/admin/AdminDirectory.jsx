import { useEffect, useState } from 'react';
import { api } from '../api';
import ConfirmAction from './ConfirmAction';

// The six real roles, matching the server's directory.Role enum exactly
// (owner, admin, security, billing, member, auditor). Anything not in this
// set is rejected by the server with a 400 (`unknown role`), so the picker
// must never offer a value outside it.
const ROLES = ['owner', 'admin', 'security', 'billing', 'member', 'auditor'];

/**
 * The Directory: who is in the organization, and the governance actions that
 * apply to them and to the Bots.
 *
 * Every mutating action routes through <ConfirmAction>, so none of them fire
 * until a typed confirmation matches AND a reason is entered. The reason is
 * passed to the API so it reaches the audit log; the client only names the
 * action, the server enforces the RBAC capability.
 */
export default function AdminDirectory() {
  const [members, setMembers] = useState(null);
  const [agents, setAgents] = useState([]);   // the Bots archive-memory can target
  const [error, setError] = useState('');
  const [pending, setPending] = useState(null);   // the action awaiting confirmation
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  async function load() {
    setError('');
    try {
      // The Bots are loaded alongside the members so archive-memory can target
      // a REAL agent id (a Bot's slugged name, e.g. 'chief'), not the operator's
      // user subject. The agent list is the only place that id exists.
      const [dir, roster] = await Promise.all([api.admin.directory(), api.agents()]);
      setMembers(dir?.members || []);
      setAgents(roster?.agents || []);
    } catch (e) {
      setError('Could not load the directory.');
    }
  }

  useEffect(() => { load(); }, []);

  // Each descriptor names the confirm token, the human title, and the call to
  // make once confirmed. `run(reason)` is what actually reaches the API.
  function suspendAction(m) {
    return {
      title: `Suspend ${m.subject}`,
      description: 'A suspended member keeps no active session and cannot act until reactivated.',
      confirmToken: m.subject,
      confirmLabel: 'Suspend member',
      run: (reason) => api.admin.suspend(m.subject, reason),
    };
  }
  function reactivateAction(m) {
    return {
      title: `Reactivate ${m.subject}`,
      description: 'Restores this member to active.',
      confirmToken: m.subject,
      confirmLabel: 'Reactivate member',
      danger: false,
      run: (reason) => api.admin.reactivate(m.subject, reason),
    };
  }
  function changeRoleAction(m, role) {
    return {
      title: `Change ${m.subject} to ${role}`,
      description: `This changes what ${m.subject} is allowed to do across the organization.`,
      confirmToken: m.subject,
      confirmLabel: `Set role to ${role}`,
      danger: role === 'owner',
      run: (reason) => api.admin.changeRole(m.subject, { role, reason }),
    };
  }
  function resetOnboardingAction() {
    const target = entrypoint?.name || 'Chief';
    return {
      title: `Reset onboarding for ${target}`,
      description: 'Puts the entrypoint Bot back through onboarding: its starter thread is '
        + 'cleared and reseeded. Memory and audit history are untouched.',
      confirmToken: 'reset',
      confirmLabel: 'Reset onboarding',
      // reset-onboarding is always about the entrypoint Bot, so the console
      // sends NO id: the server resolves the caller's own entrypoint Bot. There
      // is no user-subject-as-agent-id any more.
      run: (reason) => api.admin.resetOnboarding(reason),
    };
  }
  function archiveMemoryAction(agent) {
    return {
      title: `Archive memory for ${agent.name}`,
      description: 'Revokes (does not delete) this Bot\u2019s accumulated memory, so it stops '
        + 'appearing in future prompts. Evidence and audit rows are never touched.',
      confirmToken: agent.name,
      confirmLabel: 'Archive memory',
      // archive-memory targets a SPECIFIC Bot, so it sends that Bot's real
      // agent id (its slugged name, e.g. 'chief') from the roster -- never a
      // user subject.
      run: (reason) => api.admin.archiveMemory(agent.agentId, reason),
    };
  }

  async function confirm(reason) {
    setBusy(true);
    setActionError('');
    try {
      // The reason is recorded either way: passing it forward keeps the audit
      // log honest even for actions whose demo stub ignores it.
      await pending.run(reason);
      setPending(null);
      await load();
    } catch (e) {
      setActionError(e?.message || 'That action could not be completed.');
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <div className="page">
        <div className="empty"><strong>{error}</strong>
          <button type="button" className="ghost" onClick={load}>Try again</button></div>
      </div>
    );
  }
  if (members === null) return <div className="page"><p className="hint-text">Loading directory…</p></div>;

  // The entrypoint 'Chief', if the org has one. reset-onboarding acts on it,
  // and it is the default archive-memory target.
  const entrypoint = agents.find((a) => a.entrypoint === true) || null;

  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Directory</h1><p>Members of the organization and the Bots you govern.</p></div>
      </header>

      <section className="card-list">
        {members.map((m) => (
          <div key={m.subject} className="admin-row">
            <div className="admin-row-head">
              <strong>{m.subject}</strong>
              <span className={`state-chip ${m.state === 'active' ? 'cc-tone-ok' : 'cc-tone-warn'}`}>
                <i className="cc-dot" aria-hidden="true" />{m.state}
              </span>
            </div>
            <div className="admin-row-meta">
              <span>Role: <strong>{m.role}</strong></span>
              {m.scope && <span>Scope: {m.scope}</span>}
            </div>
            <div className="admin-row-actions">
              {m.state === 'suspended'
                ? <button type="button" className="ghost" onClick={() => setPending(reactivateAction(m))}>Reactivate</button>
                : <button type="button" className="ghost" onClick={() => setPending(suspendAction(m))}>Suspend</button>}

              <label className="admin-role-select">
                Change role
                <select
                  value={m.role}
                  onChange={(e) => { if (e.target.value !== m.role) setPending(changeRoleAction(m, e.target.value)); }}
                  aria-label={`Change role for ${m.subject}`}
                >
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </label>
            </div>
          </div>
        ))}
      </section>

      {/* The Bots the operator governs. reset-onboarding acts on the entrypoint
          'Chief' (the server resolves it, so no id is sent); archive-memory
          targets a SPECIFIC Bot, so each row carries that Bot's real agent id.
          Keying these on the roster -- not on a member's user subject -- is
          what makes a real console click resolve instead of 404. */}
      <section className="card-list">
        <header className="page-head"><div><h2>Bots</h2></div></header>
        {agents.length === 0 && <p className="hint-text">No Bots to govern yet.</p>}
        {entrypoint && (
          <div className="admin-row" key={`reset-${entrypoint.agentId}`}>
            <div className="admin-row-head"><strong>{entrypoint.name}</strong>
              <span className="state-chip cc-tone-ok"><i className="cc-dot" aria-hidden="true" />entrypoint</span>
            </div>
            <div className="admin-row-actions">
              <button type="button" className="ghost"
                onClick={() => setPending(resetOnboardingAction())}>Reset onboarding</button>
            </div>
          </div>
        )}
        {agents.map((a) => (
          <div key={`archive-${a.agentId}`} className="admin-row">
            <div className="admin-row-head"><strong>{a.name}</strong>
              {a.entrypoint && <span className="state-chip cc-tone-ok"><i className="cc-dot" aria-hidden="true" />entrypoint</span>}
            </div>
            <div className="admin-row-actions">
              <button type="button" className="danger"
                onClick={() => setPending(archiveMemoryAction(a))}>Archive memory</button>
            </div>
          </div>
        ))}
      </section>

      {pending && (
        <ConfirmAction
          title={pending.title}
          description={pending.description}
          confirmToken={pending.confirmToken}
          confirmLabel={pending.confirmLabel}
          danger={pending.danger !== false}
          busy={busy}
          error={actionError}
          onConfirm={confirm}
          onCancel={() => { setPending(null); setActionError(''); }}
        />
      )}
    </div>
  );
}
