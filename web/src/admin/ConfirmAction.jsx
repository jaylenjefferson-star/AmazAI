import { useState } from 'react';

/**
 * The blueprint's destructive-action UX, one place.
 *
 * A governance or destructive action does not fire on a single click. The
 * person must (1) type an exact confirmation token -- the subject id, agent
 * id, or a keyword -- so a mis-click on the wrong row cannot go through, and
 * (2) write a reason, because every one of these actions lands in the
 * append-only audit log and a reason is what makes that log worth keeping.
 *
 * The call is not made until BOTH are satisfied: the confirm button stays
 * disabled while the typed value does not match `confirmToken` or the reason
 * is blank. `onConfirm(reason)` is only ever invoked past that gate -- which
 * is the behaviour the component tests pin down.
 */
export default function ConfirmAction({
  title, description, confirmToken, confirmLabel = 'Confirm', danger = true,
  onConfirm, onCancel, busy = false, error = '',
}) {
  const [typed, setTyped] = useState('');
  const [reason, setReason] = useState('');

  const matches = typed.trim() === String(confirmToken).trim();
  const hasReason = reason.trim().length > 0;
  const ready = matches && hasReason && !busy;

  return (
    <div className="admin-confirm" role="dialog" aria-label={title}>
      <h3>{title}</h3>
      {description && <p className="admin-confirm-desc">{description}</p>}

      <label className="admin-field">
        <span>Type <code>{confirmToken}</code> to confirm</span>
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          aria-label="Confirmation"
          autoComplete="off"
        />
      </label>

      <label className="admin-field">
        <span>Reason (recorded in the audit log)</span>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-label="Reason"
          placeholder="Why are you doing this?"
        />
      </label>

      {error && <div className="admin-error" role="alert">{error}</div>}

      <div className="admin-confirm-actions">
        <button
          type="button"
          className={danger ? 'danger' : 'primary'}
          disabled={!ready}
          onClick={() => onConfirm(reason.trim())}
        >
          {busy ? '…' : confirmLabel}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
