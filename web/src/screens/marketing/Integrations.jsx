import PublicShell from '../../components/PublicShell';

const AVAILABLE = [
  { name: 'Slack', blurb: 'Read channels an agent was granted, or post as itself — posting sits on the always-approve floor and can never be pre-approved away.' },
];

const COMING_SOON = [
  { name: 'Google Workspace', blurb: 'Gmail, Calendar, and Docs — scoped read and draft actions first.' },
  { name: 'GitHub', blurb: 'Issues, pull requests, and CI status for engineering agents.' },
  { name: 'Notion', blurb: 'Read and draft pages inside a workspace an agent was granted.' },
  { name: 'HubSpot', blurb: 'CRM records and pipeline visibility for customer-facing agents.' },
  { name: 'Zoom / Calendars', blurb: 'Scheduling and meeting notes without a shared inbox.' },
  { name: 'Stripe', blurb: 'Billing lookups for founder-operations agents — read-only to start.' },
];

/**
 * A connector never hands an agent a raw API key. It goes through a proxy
 * that injects the credential on the other side and only ever sees an
 * account reference — permission is still decided entirely on AmazAI's
 * side, per agent, per action.
 */
export default function Integrations() {
  return (
    <PublicShell wide>
      <section className="about-hero">
        <h1>Connect real tools, without handing over the keys.</h1>
        <p>
          A connector is a way to reach an API and a place for its credential
          to live — never a second place where permission gets decided. That
          stays in AmazAI, per agent, per action.
        </p>
      </section>

      <section className="about-contact">
        <h2>Available now</h2>
        <div className="about-grid">
          {AVAILABLE.map((c) => (
            <div key={c.name} className="about-card">
              <h2>{c.name}</h2>
              <p>{c.blurb}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="about-contact">
        <h2>Coming soon</h2>
        <div className="about-grid">
          {COMING_SOON.map((c) => (
            <div key={c.name} className="about-card muted">
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
