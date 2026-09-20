import { useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Connector management is intentionally separate from agent grants. Connecting
 * an account gives the organisation a capability; an agent receives none of
 * it until it is explicitly granted on that companion's profile.
 */
export default function Connectors({ embedded = false }) {
  const [catalog, setCatalog] = useState([]);
  const [apps, setApps] = useState([]);
  const [appQuery, setAppQuery] = useState('');
  const [nextCursor, setNextCursor] = useState('');
  const [installed, setInstalled] = useState({});
  const [accounts, setAccounts] = useState({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function reload() {
    setError('');
    try {
      const [available, appPage, current, accountRows] = await Promise.all([
        api.connectorCatalog(), api.connectorApps(appQuery), api.connectors(), api.connectorAccounts(),
      ]);
      setCatalog(available.catalog || []);
      setApps(appPage.apps || []);
      setNextCursor(appPage.pageInfo?.end_cursor || '');
      setInstalled(Object.fromEntries((current.connectors || []).map((c) => [c.connectorId, c])));
      setAccounts(Object.fromEntries((accountRows.accounts || []).map((a) => [a.app, a])));
    } catch (err) {
      setError(err.message || 'Could not load connectors.');
    }
  }

  useEffect(() => {
    const timer = setTimeout(() => reload(), 250);
    return () => clearTimeout(timer);
  }, [appQuery]); // eslint-disable-line react-hooks/exhaustive-deps

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

    async function connectApp(app) {
      setBusy(app.slug);
      setError('');
      try {
        const token = await api.connectToken(`pipedream:${app.slug}`);
        const url = token.connectLinkUrl || token.connect_link_url || token.url;
        if (!url) throw new Error('Pipedream did not return a Connect Link URL.');
        window.open(url, '_blank', 'noopener,noreferrer');
      } catch (err) {
        setError(err.message || 'Could not start Pipedream Connect.');
      } finally {
        setBusy('');
      }
    }

    async function loadMoreApps() {
      if (!nextCursor) return;
      setBusy('more-apps');
      try {
        const page = await api.connectorApps(appQuery, nextCursor);
        setApps((current) => [...current, ...(page.apps || [])]);
        setNextCursor(page.pageInfo?.end_cursor || '');
      } catch (err) {
        setError(err.message || 'Could not load more apps.');
      } finally {
        setBusy('');
      }
    }
  }

  return (
    // Embedded (in the Marketplace) the page frame and heading belong to the
    // host; standing alone at /connectors it keeps its own.
    <div className={embedded ? undefined : 'page'}>
      {embedded ? (
        <div className="mk-bar"><h3 className="mk-h">Connect a tool</h3>
          <button className="btn-link" onClick={reload} disabled={Boolean(busy)}>Refresh</button></div>
      ) : (
        <header className="page-head">
          <div><h1>Connectors</h1><p>Browse 1,000+ apps through Pipedream. Connecting an app does not grant it to any companion.</p></div>
          <button className="btn-link" onClick={reload} disabled={Boolean(busy)}>Refresh</button>
        </header>
      )}
      {error && <div className="empty"><strong>Connector setup needs attention</strong><span>{error}</span></div>}
      <section className="section-block">
        <div className="section-label">Available apps</div>
        <input
          className="text-input"
          value={appQuery}
          onChange={(event) => setAppQuery(event.target.value)}
          placeholder="Search apps, for example Google Drive or GitHub"
          aria-label="Search available apps"
        />
        <div className="row-list">
          {apps.map((app) => (
            <article className="row-card" key={app.slug}>
              {app.icon ? <img className="artifact-glyph" src={app.icon} alt="" /> : <span className="artifact-glyph" aria-hidden="true">⌁</span>}
              <div className="row-body">
                <strong>{app.name}</strong>
                <span>{app.description || 'Connect this app through Pipedream.'}</span>
                {app.categories?.length > 0 && <span>{app.categories.slice(0, 3).join(' · ')}</span>}
              </div>
              <button className="primary" disabled={busy === app.slug} onClick={() => connectApp(app)}>
                Connect with Pipedream
              </button>
            </article>
          ))}
        </div>
        {!apps.length && !error && <div className="empty">No matching apps found.</div>}
        {nextCursor && <button className="ghost" disabled={busy === 'more-apps'} onClick={loadMoreApps}>Load more apps</button>}
      </section>
      <div className="section-label">Agent-enabled connectors</div>
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
