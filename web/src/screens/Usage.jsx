import { useMemo } from 'react';
import Companion from '../characters/Companion';
import { fixtureAgents } from '../fixtures';
import { DEMO_DATA } from '../fixtures';

const MICRO = 1_000_000;
const usd = (micros) => `$${(micros / MICRO).toFixed(2)}`;
const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/**
 * Usage.
 *
 * Every number on this screen is demo data and says so, loudly and in more
 * than one place. A usage screen that looks authoritative while showing
 * invented consumption is worse than no usage screen: it is the one surface
 * where being wrong quietly costs money.
 *
 * The shape matches `services/amazai/usage.py` exactly — same fields, same
 * micro-dollar integers, same statuses — so wiring it to the real ledger is
 * replacing the fixture call and deleting the banner.
 */
function demoSummary(agents) {
  const models = [
    { id: 'us.anthropic.claude-opus-5', label: 'Opus 5', share: 0.58 },
    { id: 'us.anthropic.claude-sonnet-5', label: 'Sonnet 5', share: 0.31 },
    { id: 'us.anthropic.claude-haiku-4-5', label: 'Haiku 4.5', share: 0.11 },
  ];
  const spent = 41_820_000;           // $41.82 in micro-dollars
  const limit = 200 * MICRO;
  const now = Date.now();

  return {
    month: new Date().toISOString().slice(0, 7),
    spentMicros: spent,
    limitMicros: limit,
    perRunMicros: 5 * MICRO,
    warnFraction: 0.8,
    totalTokens: 2_486_000,
    runs: 61,
    byModel: models.map((m) => ({
      ...m,
      costMicros: Math.round(spent * m.share),
      tokens: Math.round(2_486_000 * m.share),
    })),
    byAgent: agents.slice(0, 4).map((a, i) => ({
      ...a,
      costMicros: Math.round(spent * [0.44, 0.27, 0.18, 0.11][i]),
      runs: [26, 17, 11, 7][i],
    })),
    recent: [
      { id: 'r1', agent: 'Ansel', model: 'Opus 5', tokens: 48_200, micros: 1_240_000,
        status: 'finalized', at: new Date(now - 9e5).toISOString() },
      { id: 'r2', agent: 'Pell', model: 'Sonnet 5', tokens: 12_400, micros: 186_000,
        status: 'finalized', at: new Date(now - 26e5).toISOString() },
      { id: 'r3', agent: 'Wren', model: 'Haiku 4.5', tokens: 6_100, micros: 31_000,
        status: 'estimated', at: new Date(now - 41e5).toISOString() },
      { id: 'r4', agent: 'Moss', model: 'Sonnet 5', tokens: 0, micros: 0,
        status: 'failed', at: new Date(now - 72e5).toISOString() },
    ],
  };
}

export default function Usage() {
  if (!DEMO_DATA) {
    return (
      <div className="page">
        <header className="page-head">
          <div><h1>Usage</h1><p>Token and cost records from your live ledger.</p></div>
          <span className="state-chip cc-tone-ok"><i className="cc-dot" aria-hidden="true" />Live</span>
        </header>
        <div className="empty">
          <strong>No usage recorded yet</strong>
          <span>Usage will appear here after a live agent completes a model call. AmazAI never substitutes projected or sample spend for real billing data.</span>
        </div>
      </div>
    );
  }
  const agents = fixtureAgents();
  const s = useMemo(() => demoSummary(agents), []);           // eslint-disable-line
  const pct = Math.min(100, (s.spentMicros / s.limitMicros) * 100);
  const level = pct >= 90 ? 'danger' : pct >= s.warnFraction * 100 ? 'warn' : '';

  // Straight-line projection from elapsed days. Honest about being naive:
  // a projection that models weekday seasonality would need history the
  // ledger does not have yet.
  const day = new Date().getDate();
  const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
  const projected = Math.round((s.spentMicros / day) * daysInMonth);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Usage</h1>
          <p>Tokens and cost for {s.month}.</p>
        </div>
        <span className="state-chip cc-tone-warn">
          <i className="cc-dot" aria-hidden="true" />Demo data
        </span>
      </header>

      <div className="demo-banner">
        <strong>Nothing here has been spent.</strong>
        <span>
          No model call has run yet, so these figures are fixtures shaped like
          the real ledger. The Usage screen will switch to live rows the first
          time a companion actually calls a model.
        </span>
      </div>

      <section className="usage-hero">
        <div className="usage-total">
          <span className="usage-label">This month</span>
          <span className="usage-big">{usd(s.spentMicros)}</span>
          <span className="usage-sub">of {usd(s.limitMicros)} ceiling</span>
        </div>
        <div className="usage-meter">
          <div className="bar"><i className={level} style={{ width: `${pct}%` }} /></div>
          <div className="usage-meter-foot">
            <span>{pct.toFixed(0)}% used</span>
            <span>Projected {usd(projected)} by month end</span>
          </div>
        </div>
        <dl className="usage-stats">
          <div><dt>Tokens</dt><dd>{compact(s.totalTokens)}</dd></div>
          <div><dt>Runs</dt><dd>{s.runs}</dd></div>
          <div><dt>Per-run cap</dt><dd>{usd(s.perRunMicros)}</dd></div>
        </dl>
      </section>

      <section className="card-list">
        <h2 className="section-title">By model</h2>
        {s.byModel.map((m) => (
          <div key={m.id} className="usage-row">
            <div className="usage-row-head">
              <strong>{m.label}</strong>
              <span>{usd(m.costMicros)} · {compact(m.tokens)} tokens</span>
            </div>
            <div className="bar">
              <i style={{ width: `${(m.costMicros / s.spentMicros) * 100}%` }} />
            </div>
          </div>
        ))}
      </section>

      <section className="card-list">
        <h2 className="section-title">By companion</h2>
        {s.byAgent.map((a) => (
          <div key={a.agentId} className="usage-row with-face">
            <Companion archetype={a.archetype} color={a.color} state="idle"
                       size={30} name={a.name} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="usage-row-head">
                <strong>{a.name}</strong>
                <span>{usd(a.costMicros)} · {a.runs} runs</span>
              </div>
              <div className="bar">
                <i style={{ width: `${(a.costMicros / s.spentMicros) * 100}%`,
                            background: a.color }} />
              </div>
            </div>
          </div>
        ))}
      </section>

      <section className="card-list">
        <h2 className="section-title">Recent runs</h2>
        <div className="run-table" role="table">
          {s.recent.map((r) => (
            <div key={r.id} className="run-row" role="row">
              <span className="run-agent">{r.agent}</span>
              <span className="run-model">{r.model}</span>
              <span className="run-tokens num">{compact(r.tokens)}</span>
              <span className="run-cost num">{usd(r.micros)}</span>
              <span className={`run-status s-${r.status}`}>{r.status}</span>
            </div>
          ))}
        </div>
        <p className="hint-text">
          <strong>estimated</strong> is reserved before a call and counts against
          the budget; <strong>finalized</strong> is what the provider reported;
          <strong> failed</strong> releases its reservation and counts nothing.
        </p>
      </section>
    </div>
  );
}
