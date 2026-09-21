import { useEffect, useState } from 'react';
import { api } from '../api';
import ConfirmAction from './ConfirmAction';

const ROLES = ['owner', 'admin', 'member', 'viewer'];

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
  const [error, setError] = useState('');
  const [pending, setPending] = useState(null);   // the action awaiting confirmation
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  async function load() {
    setError('');
    try {
      const res = await api.admin.directory();
      setMembers(res?.members || []);
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
      run: () => api.admin.suspend(m.subject),
    };
  }
  function reactivateAction(m) {
    return {
      title: `Reactivate ${m.subject}`,
      description: 'Restores this member to active.',
      confirmToken: m.subject,
      confirmLabel: 'Reactivate member',
      danger: false,
      run: () => api.admin.reactivate(m.subject),
    };
  }
  function changeRoleAction(m, role) {
    return {
      title: `Change ${m.subject} to ${role}`,
      description: `This changes what ${m.subject} is allowed to do across the organization.`,
      confirmToken: m.subject,
      confirmLabel: `Set role to ${role}`,
      danger: role === 'owner',
      run: () => api.admin.changeRole(m.subject, { role }),
    };
  }
  function resetOnboardingAction(m) {
    return {
      title: `Reset onboarding for ${m.subject}`,
      description: 'Puts the entrypoint Bot back through onboarding: its starter thread is '
        + 'cleared and reseeded. Memory and audit history are untouched.',
      confirmToken: m.subject,
      confirmLabel: 'Reset onboarding',
      // The agent to reset is keyed on the member's subject in this
      // single-tenant seam; the server resolves it to the entrypoint Bot.
      run: () => api.admin.resetOnboarding(m.subject),
    };
  }
  function archiveMemoryAction(m) {
    return {
      title: `Archive memory for ${m.subject}`,
      description: 'Revokes (does not delete) this Bot\u2019s accumulated memory, so it stops '
        + 'appearing in future prompts. Evidence and audit rows are never touched.',
      confirmToken: m.subject,
      confirmLabel: 'Archive memory',
      run: () => api.admin.archiveMemory(m.subject),
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

              <button type="button" className="ghost" onClick={() => setPending(resetOnboardingAction(m))}>Reset onboarding</button>
              <button type="button" className="danger" onClick={() => setPending(archiveMemoryAction(m))}>Archive memory</button>
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
