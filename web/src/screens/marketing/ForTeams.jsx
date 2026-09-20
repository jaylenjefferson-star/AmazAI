import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';

const FEATURES = [
  { title: 'Shared workspace', body: 'One team, one set of agents, one place to see everything they are working on - not a separate login for every person who needs a companion.' },
  { title: 'Governed collaboration', body: 'Agents can be shared across a team without sharing the credentials or the approval authority behind them. Who can approve what is a setting, not a workaround.' },
  { title: 'Admin controls', body: 'Add or remove teammates, set budgets per agent or per person, and revoke a connector grant instantly across the whole team.' },
  { title: 'Usage visibility', body: 'See credit spend, approval activity, and connector usage across the team in one place, not scattered across individual accounts.' },
];

export default function ForTeams() {
  return (
    <PublicShell wide>
      <section className="about-hero">
        <h1>For Teams</h1>
        <p>
          Startup and SMB plans add shared workspaces, team credits, approval
          controls, audit history, and administration on top of everything
          in the individual plans.
        </p>
      </section>

      <section className="about-grid">
        {FEATURES.map((f) => (
          <div key={f.title} className="about-card">
            <h2>{f.title}</h2>
            <p>{f.body}</p>
          </div>
        ))}
      </section>

      <section className="about-contact">
        <h2>Talk to us</h2>
        <p>Tell us the size of your team and what you want your first agents to do, and we will help you set up a workspace.</p>
        <Link to="/contact" className="primary" style={{ display: 'inline-block', textDecoration: 'none' }}>
          Contact / Demo
        </Link>
      </section>
    </PublicShell>
  );
}
