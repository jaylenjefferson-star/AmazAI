import { useEffect, useState } from 'react';
import { applyMode, effectiveMode, nextMode, storedMode } from '../theme';

const ICON = { light: '☀', dark: '☾', system: '◐' };
const LABEL = { light: 'Light', dark: 'Dark', system: 'Match system' };

function ThemeButton() {
  const [mode, setMode] = useState(storedMode);

  useEffect(() => { applyMode(mode); }, [mode]);

  // Following the system means following it as it changes, not only at load.
  useEffect(() => {
    if (mode !== 'system') return undefined;
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) return undefined;
    const onChange = () => applyMode('system');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [mode]);

  return (
    <button className="ghost sm theme-btn" onClick={() => setMode(nextMode(mode))}
            title={`Theme: ${LABEL[mode]}${mode === 'system' ? ` (${effectiveMode(mode)})` : ''}`}
            aria-label={`Theme: ${LABEL[mode]}. Click to change.`}>
      <span aria-hidden="true">{ICON[mode]}</span>
    </button>
  );
}

/**
 * Global chrome. Two things live here permanently and everything else is
 * secondary: whether the socket is up, and how much has been spent this
 * month. Both are properties of a system that keeps running when the console
 * is closed, so neither should require navigating anywhere to see.
 */
export default function Topbar({
  region, spend, budget, wsStatus, onSignOut, onToggleSidebar, onTogglePanel,
}) {
  const connected = wsStatus === 'connected';
  const pct = budget > 0 ? Math.min(100, (spend / budget) * 100) : 0;
  const level = pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : '';

  return (
    <header className="topbar">
      <button className="ghost sm nav-only" onClick={onToggleSidebar} aria-label="Toggle navigation">☰</button>

      <div className="brand">
        <span className="mark" aria-hidden="true">A</span>
        <span>AmazAI</span>
        <span className="env">{region}</span>
      </div>

      <span className="spacer" />

      {budget > 0 && (
        <div className="spend" title={`Month to date across all agents, against a ${budget} USD ceiling`}>
          <span className="label">MTD</span>
          <span className="money">
            <b>${spend.toFixed(2)}</b> <span className="of">/ ${budget}</span>
          </span>
          <div className="bar" style={{ flex: 1, margin: 0 }}>
            <i className={level} style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}

      <div className="conn" title={`Live connection: ${wsStatus}`}>
        <span className={`dot ${connected ? '' : 'pulse'}`}
              style={{ background: connected ? 'var(--ok)' : 'var(--warn)', color: 'var(--warn)' }} />
        <span>{connected ? 'live' : wsStatus}</span>
      </div>

      <ThemeButton />
      <button className="ghost sm panel-toggle" onClick={onTogglePanel}>Panel</button>
      <button className="ghost sm" onClick={onSignOut}>Sign out</button>
    </header>
  );
}
