import { useEffect, useState } from 'react';

function remaining(expiresAt) {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return null;
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Every field here comes from the tool call's arguments, never from
 * model-authored prose. An injected model must not be able to write its own
 * approval card.
 */
export default function ApprovalCard({ approval, onDecide }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(() => remaining(approval.expiresAt));

  useEffect(() => {
    const t = setInterval(() => setLeft(remaining(approval.expiresAt)), 1000);
    return () => clearInterval(t);
  }, [approval.expiresAt]);

  const settled = approval.status !== 'pending';
  const expired = !left && !settled;

  async function decide(approve) {
    setBusy(true);
    try { await onDecide(approve, note.trim() || undefined); }
    finally { setBusy(false); }
  }

  const target = approval.target || {};

  return (
    <div className={`approval ${approval.risk === 'high' ? 'high' : ''}`}>
      <h4>
        {settled ? `Approval ${approval.status}`
          : expired ? 'Approval expired — denied'
          : 'Approval required'}
      </h4>

      <dl>
        <dt>Action</dt><dd>{approval.action}</dd>
        {Object.entries(approval.arguments || {}).map(([k, v]) => (
          <span key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
          </span>
        ))}
        {target.account && <><dt>Account</dt><dd>{target.account}{target.env ? ` (${target.env})` : ''}</dd></>}
        {target.region && <><dt>Region</dt><dd>{target.region}</dd></>}
        {target.repo && <><dt>Repo</dt><dd>{target.repo}</dd></>}
        {approval.reversible != null && (
          <><dt>Reversible</dt><dd>{approval.reversible ? 'yes' : 'no'}</dd></>
        )}
        {approval.why && <><dt>Why</dt><dd style={{ fontFamily: 'inherit' }}>{approval.why}</dd></>}
        {approval.requestedBy?.agentId && (
          <><dt>Requested by</dt><dd>
            {approval.requestedBy.agentId}
            {approval.requestedBy.routineId ? ` · routine ${approval.requestedBy.routineId}` : ''}
          </dd></>
        )}
      </dl>

      {!settled && !expired && (
        <>
          <input placeholder="Optional note…" value={note}
                 onChange={(e) => setNote(e.target.value)} style={{ marginBottom: 10 }} />
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => decide(true)}>Approve</button>
            <button className="danger" disabled={busy} onClick={() => decide(false)}>Deny</button>
            <span className="expiry">expires in {left}</span>
          </div>
        </>
      )}
      {settled && approval.note && (
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>Note: {approval.note}</div>
      )}
    </div>
  );
}
