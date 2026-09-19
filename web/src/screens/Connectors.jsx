import { useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Connector management is intentionally separate from agent grants. Connecting
 * an account gives the organisation a capability; an agent receives none of
 * it until it is explicitly granted on that companion's profile.
 */
export default function Connectors() {
  const [catalog, setCatalog] = useState([]);
  const [installed, setInstalled] = useState({});
  const [accounts, setAccounts] = useState({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function reload() {
    setError('');
    try {
      const [available, current, accountRows] = await Promise.all([
        api.connectorCatalog(), api.connectors(), api.connectorAccounts(),
      ]);
      setCatalog(available.catalog || []);
      setInstalled(Object.fromEntries((current.connectors || []).map((c) => [c.connectorId, c])));
      setAccounts(Object.fromEntries((accountRows.accounts || []).map((a) => [a.app, a])));
    } catch (err) {
      setError(err.message || 'Could not load connectors.');
    }
  }

  useEffect(() => { reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function connect(spec) {
    setBusy(spec.connectorId);
    setError('');
    try {
      const token = await api.connectToken(spec.connectorId);
      const url = token.connectLinkUrl || token.connect_link_url || token.url;
      if (!url) throw new Error('Pipedream did not return a Connect Link URL.');
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      setError(err.message || 'Could not start Pipedream Connect.');
    } finally {
      setBusy('');
    }
  }

  async function install(spec) {
    const account = accounts[spec.app];
    if (!account) return connect(spec);
    setBusy(spec.connectorId);
    setError('');
    try {
      await api.installConnector(spec.connectorId, account.accountId,
        spec.actions.map((action) => action.tool));
      await reload();
    } catch (err) {
      setError(err.message || 'Could not install connector.');
    } finally {
      setBusy('');
    }
  }

  async function revoke(spec) {
    setBusy(spec.connectorId);
    try {
      await api.revokeConnector(spec.connectorId);
      await reload();
    } catch (err) {
      setError(err.message || 'Could not revoke connector.');
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Connectors</h1><p>Connect accounts through Pipedream, then grant only the tools each companion needs.</p></div>
        <button className="btn-link" onClick={reload} disabled={Boolean(busy)}>Refresh</button>
      </header>
      {error && <div className="empty"><strong>Connector setup needs attention</strong><span>{error}</span></div>}
      <div className="row-list">
        {catalog.map((spec) => {
          const current = installed[spec.connectorId];
          const account = accounts[spec.app];
          return (
            <article className="row-card" key={spec.connectorId}>
              <span className="artifact-glyph" aria-hidden="true">⌁</span>
              <div className="row-body">
                <strong>{spec.name}</strong>
                <span>{spec.description}</span>
                <span>{spec.actions.map((a) => a.tool).join(' · ')}</span>
              </div>
              <div style={{ display: 'grid', gap: 8, justifyItems: 'end' }}>
                {current ? <>
                  <span className="state-chip cc-tone-ok"><i className="cc-dot" aria-hidden="true" />Installed</span>
                  <button className="ghost sm" disabled={busy === spec.connectorId} onClick={() => revoke(spec)}>Revoke</button>
                </> : <>
                  {account && <span className="state-chip cc-tone-neutral"><i className="cc-dot" aria-hidden="true" />Account connected</span>}
                  <button className="primary" disabled={busy === spec.connectorId} onClick={() => install(spec)}>
                    {account ? 'Install for AmazAI' : 'Connect with Pipedream'}
                  </button>
                </>}
              </div>
            </article>
          );
        })}
      </div>
      {!catalog.length && !error && <div className="empty">Loading your connector catalog…</div>}
      <p className="hint-text">Connecting an account does not grant it to every agent. Grant access from an agent’s setup, and write or destructive actions still require approval.</p>
    </div>
  );
}
