import { useEffect, useState } from 'react';
import { api } from '../api';
import ConfirmAction from './ConfirmAction';

/**
 * The kill switch: freeze or unfreeze the whole organization.
 *
 * Freezing is the single most destructive control on the surface -- it stops
 * every run for everyone -- so it goes behind the same typed-confirm + reason
 * gate as the Directory's actions. Unfreezing is gated too: bringing an org
 * back from a freeze is a decision worth recording a reason for.
 */
export default function AdminKillSwitch() {
  const [state, setState] = useState(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  async function load() {
    setError('');
    try {
      setState(await api.admin.killswitch());
    } catch (e) {
      setError('Could not read the kill switch status.');
    }
  }

  useEffect(() => { load(); }, []);

  async function confirm(reason) {
    setBusy(true);
    setActionError('');
    try {
      await api.admin.setKillswitch({ frozen: pending.frozen, reason });
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
  if (state === null) return <div className="page"><p className="hint-text">Loading kill switch…</p></div>;

  const frozen = !!state.frozen;

  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Kill switch</h1><p>Freeze the organization to stop every run at once.</p></div>
        <span className={`state-chip ${frozen ? 'cc-tone-danger' : 'cc-tone-ok'}`}>
          <i className="cc-dot" aria-hidden="true" />{frozen ? 'Frozen' : 'Running'}
        </span>
      </header>

      <section className="card-list">
        <div className="admin-row">
          <div className="admin-row-head">
            <strong>{frozen ? 'The organization is frozen' : 'The organization is running'}</strong>
          </div>
          {frozen && state.reason && (
            <div className="admin-row-meta"><span>Reason: {state.reason}</span></div>
          )}
          {frozen && state.setBy && (
            <div className="admin-row-meta"><span>Frozen by {state.setBy}{state.setAt ? ` at ${state.setAt}` : ''}</span></div>
          )}
          <div className="admin-row-actions">
            {frozen
              ? <button type="button" className="primary" onClick={() => setPending({ frozen: false })}>Unfreeze</button>
              : <button type="button" className="danger" onClick={() => setPending({ frozen: true })}>Freeze organization</button>}
          </div>
        </div>
      </section>

      {pending && (
        <ConfirmAction
          title={pending.frozen ? 'Freeze the organization' : 'Unfreeze the organization'}
          description={pending.frozen
            ? 'This stops every run for everyone until you unfreeze. In-flight work is halted.'
            : 'This lets runs start again across the organization.'}
          confirmToken={pending.frozen ? 'FREEZE' : 'UNFREEZE'}
          confirmLabel={pending.frozen ? 'Freeze now' : 'Unfreeze now'}
          danger={pending.frozen}
          busy={busy}
          error={actionError}
          onConfirm={confirm}
          onCancel={() => { setPending(null); setActionError(''); }}
        />
      )}
    </div>
  );
}
