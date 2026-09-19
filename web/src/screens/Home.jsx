import { Link } from 'react-router-dom';
import Companion from '../characters/Companion';
import { useAuth0 } from '../auth0';
import { fixtureAgents, fixtureInbox } from '../fixtures';
import { useAgents } from '../hooks/useAgents';

/**
 * Home is an inbox, not a dashboard.
 *
 * The first question on opening a console that ran while you were asleep is
 * "what needs me?", not "how are my metrics". So approvals come first,
 * then what ran, then everything at rest.
 */
export default function Home() {
  const { user } = useAuth0();
  const first = (user?.given_name || user?.name || '').split(' ')[0];
  const inbox = fixtureInbox();
  const { agents, loading, error } = useAgents();
  const needsYou = inbox.filter((i) => i.kind === 'approval');

  return (
    <div className="page">
      <header className="page-head">
        <h1>{first ? `Morning, ${first}` : 'Your workspace'}</h1>
        <p>{needsYou.length
          ? `${needsYou.length} thing${needsYou.length > 1 ? 's' : ''} waiting on you.`
          : 'Nothing is waiting on you.'}</p>
      </header>

      <section className="card-list">
        <h2 className="section-title">Needs you</h2>
        {needsYou.length === 0 ? (
          <div className="rest-state">
            <Companion archetype="pebble" color="#12a594" state="idle" size={56} />
            <div>
              <strong>All clear</strong>
              <span>Your companions are resting. They will wake for a schedule or a nudge.</span>
            </div>
          </div>
        ) : needsYou.map((item) => (
          <article key={item.id} className="inbox-card warn">
            <Companion archetype={item.archetype} color={item.color}
                       state="approval" size={44} name={item.agent} />
            <div className="inbox-body">
              <strong>{item.title}</strong>
              <span>{item.detail}</span>
            </div>
            <Link className="inbox-go" to="/agents/ops">Review</Link>
          </article>
        ))}
      </section>

      <section className="card-list">
        <h2 className="section-title">Your cast</h2>
        {loading && <div className="empty">Loading your companions…</div>}
        {error && <div className="empty"><strong>Control plane unavailable</strong><span>{error}</span></div>}
        {!loading && !error && agents.length === 0 && (
          <div className="empty"><strong>No companions yet</strong><span>Create your first one in Agents. Nothing is pre-filled or simulated.</span></div>
        )}
        <div className="cast-grid">
          {agents.map((a) => (
            <Link key={a.agentId} to={`/agents/${a.agentId}`} className="cast-card">
              <Companion archetype={a.archetype} color={a.color} state={a.state}
                         size={54} name={a.name} showLabel />
              <strong>{a.name}</strong>
              <span>{a.role}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
