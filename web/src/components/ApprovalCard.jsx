import { useEffect, useMemo, useState } from 'react';

function remaining(expiresAt) {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return null;
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return { text: `${m}:${String(s).padStart(2, '0')}`, ms };
}

const ACTION_LABELS = {
  'agent.create': 'Create a new agent seat',
  'memory.publish': 'Publish to shared memory',
};

function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

/**
 * Every field here comes from the tool call's arguments, never from
 * model-authored prose. An injected model must not be able to write its own
 * approval card.
 *
 * The expiry is drawn as well as counted. An approval that runs out expires
 * to DENIED — so the bar draining to empty is a safe outcome, and is allowed
 * to look like one right up until it is nearly gone.
 */
export default function ApprovalCard({ approval, onDecide }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(() => remaining(approval.expiresAt));

  useEffect(() => {
    const t = setInterval(() => setLeft(remaining(approval.expiresAt)), 1000);
    return () => clearInterval(t);
  }, [approval.expiresAt]);

  // Total window, measured once, so the bar has a stable denominator even
  // though the card may mount well after the request was made.
  const total = useMemo(() => {
    const issued = approval.requestedAt ? new Date(approval.requestedAt).getTime() : null;
    const expires = new Date(approval.expiresAt).getTime();
    return issued ? Math.max(1, expires - issued) : Math.max(1, left?.ms ?? 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval.approvalId]);

  const settled = approval.status !== 'pending';
  const expired = !left && !settled;
  const urgent = !!left && left.ms < 60000;
  const high = approval.risk === 'high';
  const irreversible = approval.reversible === false;

  async function decide(approve) {
    setBusy(true);
    try { await onDecide(approve, note.trim() || undefined); }
    finally { setBusy(false); }
  }

  const target = approval.target || {};
  const pct = left ? Math.max(0, Math.min(100, (left.ms / total) * 100)) : 0;

  return (
    <div className={`approval enter ${high ? 'high' : ''} ${settled || expired ? 'settled' : ''}`}>
      <div className="head">
        <h4>
          {settled ? `Approval ${approval.status}`
            : expired ? 'Expired — denied'
            : 'Approval required'}
        </h4>
        {!settled && !expired && (
          <span className="risk-tag">{high ? 'high risk' : 'review'}</span>
        )}
      </div>

      {!settled && !expired && (
        <div className="drain" role="presentation">
          <i className={urgent ? 'urgent' : ''} style={{ width: `${pct}%` }} />
        </div>
      )}

      {irreversible && !settled && !expired && (
        <div className="irreversible">
          <span aria-hidden="true">⚠</span>
          <span>This action cannot be undone.</span>
        </div>
      )}

      <dl>
        <dt>Action</dt><dd>{actionLabel(approval.action)}</dd>
        {Object.entries(approval.arguments || {}).map(([k, v]) => (
          <span key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
          </span>
        ))}
        {target.account && (
          <><dt>Account</dt><dd>{target.account}{target.env ? ` (${target.env})` : ''}</dd></>
        )}
        {target.region && <><dt>Region</dt><dd>{target.region}</dd></>}
        {target.repo && <><dt>Repo</dt><dd>{target.repo}</dd></>}
        {approval.reversible != null && (
          <><dt>Reversible</dt><dd>{approval.reversible ? 'yes' : 'no'}</dd></>
        )}
        {approval.why && <><dt>Why</dt><dd className="prose">{approval.why}</dd></>}
        {approval.requestedBy?.agentId && (
          <><dt>Requested by</dt><dd>
            {approval.requestedBy.agentId}
            {approval.requestedBy.routineId ? ` · routine ${approval.requestedBy.routineId}` : ''}
          </dd></>
        )}
      </dl>

      {!settled && !expired && (
        <>
          <input placeholder="Optional note, recorded either way…" value={note}
                 onChange={(e) => setNote(e.target.value)} style={{ marginBottom: 11 }} />
          <div className="actions">
            <button className={high ? 'danger' : 'primary'} disabled={busy}
                    onClick={() => decide(true)}>
              {busy ? '…' : high ? 'Approve anyway' : 'Approve'}
            </button>
            <button className="ghost" disabled={busy} onClick={() => decide(false)}>Deny</button>
            <span className={`expiry ${urgent ? 'urgent' : ''}`}>expires in {left?.text}</span>
          </div>
        </>
      )}

      {settled && approval.note && (
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>Note: {approval.note}</div>
      )}
    </div>
  );
}
