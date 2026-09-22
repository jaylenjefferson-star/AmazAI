import { useEffect, useState } from 'react';
import { api } from '../api';

/**
 * The admin audit log: read-only, chronological, no mutation controls.
 *
 * This is the append-only trail every admin action writes (services/amazai/
 * govern.py). It is joined to run evidence by correlationId, and it survives
 * the deletion of whatever it describes -- so this screen only ever reads it.
 */
export default function AdminAudit() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const res = await api.admin.audit();
        setRows(res?.audit || []);
      } catch (e) {
        setError('Could not load the audit log.');
      }
    })();
  }, []);

  if (error) return <div className="page"><div className="empty"><strong>{error}</strong></div></div>;
  if (rows === null) return <div className="page"><p className="hint-text">Loading audit log…</p></div>;

  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Audit log</h1><p>Every admin action, in order. Append-only and read-only.</p></div>
      </header>

      {rows.length === 0 ? (
        <div className="empty"><strong>Nothing recorded yet</strong>
          <span>Admin actions will appear here as they happen.</span></div>
      ) : (
        <section className="card-list">
          {rows.map((r, i) => (
            <div key={r.correlationId || i} className="admin-audit-row">
              <div className="admin-audit-head">
                <strong className="admin-audit-action">{r.action}</strong>
                <span className="admin-audit-at">{r.at}</span>
              </div>
              <div className="admin-row-meta">
                <span>Actor: {r.actorUserId || r.actorAgentId || 'system'}</span>
                {r.correlationId && <span>Correlation: <code>{r.correlationId}</code></span>}
              </div>
              {r.detail && <p className="admin-audit-detail">{r.detail}</p>}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
