import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

// Examples of popular connectors, not the whole catalog: Composio offers
// 1000+ apps and there is no allowlist, so any of them can be connected today.
const POPULAR = [
  { name: 'Slack', blurb: 'Read channels an agent was granted, or post as itself — posting sits on the always-approve floor and can never be pre-approved away.' },
  { name: 'Google Workspace', blurb: 'Gmail, Calendar, and Docs — scoped read and draft actions per agent.' },
  { name: 'GitHub', blurb: 'Issues, pull requests, and CI status for engineering agents.' },
  { name: 'Notion', blurb: 'Read and draft pages inside a workspace an agent was granted.' },
  { name: 'HubSpot', blurb: 'CRM records and pipeline visibility for customer-facing agents.' },
  { name: 'Stripe', blurb: 'Billing lookups for founder-operations agents — read-only to start.' },
];

/**
 * A connector never hands an agent a raw API key. It goes through Composio,
 * which injects the credential on the other side and only ever exposes an
 * account reference — permission is still decided entirely on AmazAI's
 * side, per agent, per action.
 */
export default function Integrations() {
  return (
    <PublicShell wide>
      <Seo
        title="Integrations"
        description="Connect 1000+ apps securely through Composio — Slack, Google Workspace, GitHub, Notion, HubSpot and more — without ever handing an agent your API keys."
        path="/integrations"
      />
      <section className="about-hero">
        <h1>Connect 1000+ apps, without handing over the keys.</h1>
        <p>
          AmazAI reaches over a thousand apps securely through Composio. A
          connector is a way to reach an API and a place for its credential to
          live — never a second place where permission gets decided. That stays
          in AmazAI, per agent, per action.
        </p>
      </section>

      <section className="about-contact">
        <h2>1000+ connectors, available today</h2>
        <p>
          There is no allowlist. Any app Composio offers can be connected now,
          and connecting it makes it usable by your companions. The examples
          below are a few of the most popular — the full catalog runs to more
          than a thousand.
        </p>
      </section>

      <section className="about-contact">
        <h2>Popular connectors</h2>
        <div className="about-grid">
          {POPULAR.map((c) => (
            <div key={c.name} className="about-card">
              <h2>{c.name}</h2>
              <p>{c.blurb}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="about-contact">
        <h2>Scoped by design</h2>
        <p>
          Installing a connector at the organization level doesn't grant it to
          anyone. It has to be granted again, to one agent at a time, with the
          specific actions that agent may take — read a channel, but not
          post to it; look up a record, but not delete one. Anything on the
          always-approve floor, like posting publicly or sending on someone's
          behalf, stops for a human decision every single time, no matter how
          it was configured.
        </p>
      </section>
    </PublicShell>
  );
}
