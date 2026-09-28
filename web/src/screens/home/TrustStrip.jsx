/**
 * Trust / integration strip.
 *
 * Honesty rule from the spec: only real-or-marked-upcoming integrations.
 * Per docs/connectors.md and screens/marketing/Integrations.jsx, Slack is the
 * one connector available today; the rest are on the roadmap and are labelled
 * "Upcoming" so no partnership is implied. Wordmarks are plain text (no
 * fabricated logos), kept restrained — a quiet row, not a wall of pills.
 */
const TOOLS = [
  { name: 'Slack', upcoming: false },
  { name: 'GitHub', upcoming: true },
  { name: 'Google Drive', upcoming: true },
  { name: 'Gmail', upcoming: true },
  { name: 'Google Calendar', upcoming: true },
  { name: 'Notion', upcoming: true },
  { name: 'Microsoft', upcoming: true },
];

export default function TrustStrip() {
  return (
    <section className="mkt-trust">
      <div className="mkt-container">
        <h2 className="mkt-trust-head">
          One workspace. Your models, tools, files, teammates, and AI companions.
        </h2>
        <ul className="mkt-trust-row">
          {TOOLS.map((t) => (
            <li key={t.name} className={`mkt-trust-item${t.upcoming ? ' is-upcoming' : ''}`}>
              <span className="mkt-trust-name">{t.name}</span>
              {t.upcoming && <span className="mkt-trust-tag">Upcoming</span>}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
