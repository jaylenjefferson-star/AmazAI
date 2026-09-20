import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

/**
 * Connect an app, then it is usable. There is no catalog to pick from: every app
 * Composio offers is here, and connecting one makes it available to your Bots.
 * What a Bot may *do* in it is decided per call on the server, so reads run and
 * anything that changes data still asks first.
 *
 * The flow: Connect opens Composio's own sign-in page in a new tab (the
 * credential never touches AmazAI). When you come back to this tab, or land here
 * from the return link, we ask Composio whether the account is active and, if it
 * is, install it. There is no separate "install" step for a person to find.
 */
const idFor = (slug) => `composio:${slug}`;

function friendly(err, fallback) {
  const text = String(err?.message || '');
  // A transport failure ("Load failed", "Failed to fetch") is not something a
  // person can act on; say what they can do.
  if (!text || /load failed|failed to fetch|networkerror|network request/i.test(text)) return fallback;
  return text;
}

export function AppLogo({ app, size = 40 }) {
  const [broken, setBroken] = useState(false);
  const initial = (app.name || app.slug || '?').trim().charAt(0).toUpperCase();
  return app.logo && !broken
    ? <img className="app-logo" src={app.logo} alt="" width={size} height={size}
           loading="lazy" onError={() => setBroken(true)} />
    : <span className="app-logo fallback" aria-hidden="true" style={{ width: size, height: size }}>{initial}</span>;
}

export default function Connectors({ embedded = false }) {
  const [apps, setApps] = useState([]);
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState('');
  const [installed, setInstalled] = useState({});
  const [pending, setPending] = useState({});     // slug -> true while a sign-in tab is open
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState('');
  const [notice, setNotice] = useState('');
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  const load = useCallback(async (q) => {
    setLoading(true);
    setProblem('');
    // Settled apart: our own installed list needs no third party, so a slow
    // Composio call must not hide it.
    const [page, current] = await Promise.allSettled([api.connectorApps(q), api.connectors()]);
    if (current.status === 'fulfilled') {
      setInstalled(Object.fromEntries((current.value.connectors || []).map((c) => [c.connectorId, c])));
    }
    if (page.status === 'fulfilled') {
      setApps(page.value.apps || []);
      setCursor(page.value.pageInfo?.end_cursor || '');
    } else {
      setProblem(friendly(page.reason, 'Having trouble loading apps.'));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(query), query ? 250 : 0);
    return () => clearTimeout(t);
  }, [query, load]);

  const finish = useCallback(async (slug) => {
    // Composio is the authority on whether the person finished signing in.
    setBusy(slug);
    setProblem('');
    try {
      const row = await api.installConnector(idFor(slug));
      setPending((p) => ({ ...p, [slug]: false }));
      setInstalled((cur) => ({ ...cur, [row.connectorId]: row }));
      setNotice(`${row.name} is connected. Your Bots can use it now.`);
    } catch (err) {
      if (/not_connected|not connected yet/i.test(String(err?.message))) {
        setNotice('');   // still signing in; try again when they return
      } else {
        setProblem(friendly(err, "Couldn't finish connecting. Try again."));
      }
    } finally {
      setBusy('');
    }
  }, []);

  // Returning from Composio's sign-in tab: settle whatever is waiting.
  useEffect(() => {
    const back = () => Object.keys(pendingRef.current).filter((s) => pendingRef.current[s]).forEach(finish);
    window.addEventListener('focus', back);
    document.addEventListener('visibilitychange', back);
    return () => {
      window.removeEventListener('focus', back);
      document.removeEventListener('visibilitychange', back);
    };
  }, [finish]);

  // Landing here from Composio's return link.
  useEffect(() => {
    const slug = new URLSearchParams(window.location.search).get('connected');
    if (slug && /^[a-z0-9_-]{1,64}$/.test(slug)) finish(slug);
  }, [finish]);

  async function connect(app) {
    setBusy(app.slug);
    setProblem('');
    try {
      const link = await api.connectToken(idFor(app.slug));
      if (!link.connectLinkUrl) throw new Error('');
      window.open(link.connectLinkUrl, '_blank', 'noopener,noreferrer');
      setPending((p) => ({ ...p, [app.slug]: true }));
    } catch (err) {
      setProblem(friendly(err, `Couldn't start connecting ${app.name}. Try again.`));
    } finally {
      setBusy('');
    }
  }

  async function remove(app) {
    setBusy(app.slug);
    try {
      await api.revokeConnector(idFor(app.slug));
      setInstalled((cur) => {
        const next = { ...cur };
        delete next[idFor(app.slug)];
        return next;
      });
      setNotice(`${app.name} was removed from your Bots.`);
    } catch (err) {
      setProblem(friendly(err, `Couldn't remove ${app.name}. Try again.`));
    } finally {
      setBusy('');
    }
  }

  async function more() {
    if (!cursor) return;
    setBusy('more');
    try {
      const page = await api.connectorApps(query, cursor);
      setApps((cur) => [...cur, ...(page.apps || [])]);
      setCursor(page.pageInfo?.end_cursor || '');
    } catch (err) {
      setProblem(friendly(err, "Couldn't load more apps."));
    } finally {
      setBusy('');
    }
  }

  // Connected apps float to the top, in the order they were added.
  const mine = Object.values(installed).map((c) => ({ slug: c.app, name: c.name, logo: '', description: '', connected: true }));
  const shown = [
    ...mine.filter((m) => !query || m.name.toLowerCase().includes(query.toLowerCase())),
    ...apps.filter((a) => !installed[idFor(a.slug)]),
  ];

  return (
    <div className={embedded ? 'tools' : 'page tools'}>
      {!embedded && (
        <header className="page-head">
          <div>
            <h1>Connect a tool</h1>
            <p>Connect an app and your Bots can use it. Reading runs on its own; anything that changes something asks you first.</p>
          </div>
        </header>
      )}

      <input className="tools-search" type="search" placeholder="Search apps, like Gmail or GitHub"
             aria-label="Search apps" value={query} onChange={(e) => setQuery(e.target.value)} />

      {notice && <div className="tools-note" role="status">{notice}</div>}
      {problem && (
        <div className="tools-problem" role="alert">
          <span>{problem}</span>
          <button type="button" className="ghost sm" onClick={() => load(query)}>Retry</button>
        </div>
      )}

      {loading && !shown.length ? (
        <div className="tools-list" aria-busy="true" aria-label="Loading apps">
          {Array.from({ length: 6 }, (_, i) => <div className="tool-row skeleton" key={i} />)}
        </div>
      ) : (
        <ul className="tools-list">
          {shown.map((app) => {
            const on = !!installed[idFor(app.slug)] || app.connected;
            const waiting = pending[app.slug];
            return (
              <li className="tool-row" key={app.slug}>
                <AppLogo app={app} />
                <div className="tool-text">
                  <strong>{app.name}</strong>
                  <span>{on ? 'Connected. Your Bots can use it.'
                    : waiting ? 'Finish signing in, then come back here.'
                    : (app.description || 'Connect to use it with your Bots.')}</span>
                </div>
                {on
                  ? <button type="button" className="ghost sm" disabled={busy === app.slug} onClick={() => remove(app)}>Remove</button>
                  : waiting
                    ? <button type="button" className="primary sm" disabled={busy === app.slug} onClick={() => finish(app.slug)}>I&apos;m done</button>
                    : <button type="button" className="primary sm" disabled={busy === app.slug} onClick={() => connect(app)}>Connect</button>}
              </li>
            );
          })}
        </ul>
      )}

      {!loading && !problem && !shown.length && (
        <div className="tools-empty">No apps match &ldquo;{query}&rdquo;.</div>
      )}
      {cursor && !loading && (
        <button type="button" className="ghost tools-more" disabled={busy === 'more'} onClick={more}>
          {busy === 'more' ? 'Loading…' : 'Show more apps'}
        </button>
      )}
    </div>
  );
}
