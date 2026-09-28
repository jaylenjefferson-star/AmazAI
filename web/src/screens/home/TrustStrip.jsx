/**
 * Trust / integration strip.
 *
 * AmazAI reaches 1000+ apps through Composio (see docs/connectors.md): there
 * is no allowlist, so any app Composio offers can be connected today. The
 * credential lives with Composio as an account reference and never enters
 * AmazAI; permission is still decided per agent, per action on AmazAI's side.
 * The wordmarks below are plain-text EXAMPLES of popular connectors, not a
 * claim of partnership and not a fabricated logo wall — a quiet row, not a
 * wall of pills.
 */
const TOOLS = [
  { name: 'Slack' },
  { name: 'GitHub' },
  { name: 'Google Drive' },
  { name: 'Gmail' },
  { name: 'Google Calendar' },
  { name: 'Notion' },
  { name: 'Microsoft' },
];

export default function TrustStrip() {
  return (
    <section className="mkt-trust">
      <div className="mkt-container">
        <h2 className="mkt-trust-head">
          One workspace. Your models, tools, files, teammates, and AI companions.
        </h2>
        <p className="mkt-trust-lede">
          Connect 1000+ apps securely through Composio — your credentials stay
          with Composio and never enter AmazAI.
        </p>
        <ul className="mkt-trust-row">
          {TOOLS.map((t) => (
            <li key={t.name} className="mkt-trust-item">
              <span className="mkt-trust-name">{t.name}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
