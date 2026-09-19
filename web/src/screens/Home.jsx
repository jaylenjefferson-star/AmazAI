import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Companion from '../characters/Companion';
import { useAuth0 } from '../auth0';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';

const APPROVAL_TITLES = {
  'agent.create': 'wants to create a new agent seat',
  'memory.publish': 'proposed a shared-memory publication',
};

/**
 * Home is an inbox, not a dashboard.
 *
 * The first question on opening a console that ran while you were asleep is
 * "what needs me?", not "how are my metrics". So approvals come first,
 * then what ran, then everything at rest.
 *
 * Every card here is a real pending approval read from `/approvals` --
 * nothing is pre-scripted, and there is no fixture path in production.
 */
export default function Home() {
  const { user } = useAuth0();
  const first = (user?.given_name || user?.name || '').split(' ')[0];
  const { agents, loading, error } = useAgents();
  const [needsYou, setNeedsYou] = useState([]);
  const [inboxError, setInboxError] = useState('');

  useEffect(() => {
    api.approvals('pending').then((r) => setNeedsYou(r.approvals || []))
      .catch((e) => setInboxError(e.message));
  }, []);

  function agentFor(approval) {
    return agents.find((a) => a.agentId === approval.requestedBy?.agentId);
  }

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
        {inboxError && <div className="empty"><strong>Approvals unavailable</strong><span>{inboxError}</span></div>}
        {!inboxError && needsYou.length === 0 ? (
          <div className="rest-state">
            <Companion archetype="pebble" color="#12a594" state="idle" size={56} />
            <div>
              <strong>All clear</strong>
              <span>Your companions are resting. They will wake for a schedule or a nudge.</span>
            </div>
          </div>
        ) : needsYou.map((item) => {
          const agent = agentFor(item);
          return (
            <article key={item.approvalId} className="inbox-card warn">
              <Companion archetype={agent?.archetype || 'pebble'} color={agent?.color || '#2f6fe4'}
                         state="approval" size={44} name={agent?.name} />
              <div className="inbox-body">
                <strong>{agent?.name || 'An agent'} {APPROVAL_TITLES[item.action] || `requests ${item.action}`}</strong>
                <span>{item.why || item.target?.summary || 'Review the details before this runs.'}</span>
              </div>
              <Link className="inbox-go" to={agent ? `/agents/${agent.agentId}` : '/agents'}>Review</Link>
            </article>
          );
        })}
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
