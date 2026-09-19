import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import Timeline from '../components/Timeline';
import { api } from '../api';
import { presentAgent, useAgents } from '../hooks/useAgents';

/**
 * One companion, one thread.
 *
 * The approval card and the execution timeline were the strongest parts of
 * the previous three-panel console, and replacing that shell with a
 * mobile-first one would have orphaned both. They live here instead: a
 * focused view you open from Agents, rather than a third panel that a phone
 * has no room for.
 */
export default function Task() {
  const { agentId } = useParams();
  const { agents } = useAgents();
  const [agent, setAgent] = useState(null);
  const [items, setItems] = useState([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    api.agent(agentId).then((a) => setAgent(presentAgent(a))).catch((e) => setError(e.message));
    api.thread(`dm-${agentId}`).then((thread) => {
      setItems((thread.messages || []).map((message) => ({
        type: 'message', role: message.role, author: message.author, text: message.text,
      })));
    }).catch((e) => {
      // A newly provisioned agent has no conversation yet; a missing thread
      // is not a substitute for demo conversation history.
      if (!String(e.message).includes('404')) setError(e.message);
    });
  }, [agentId]);

  async function send(e) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !agent) return;
    setDraft('');
    setItems((current) => [...current, { type: 'message', role: 'user', author: 'you', text }]);
    try {
      await api.send(`dm-${agentId}`, text);
    } catch (err) {
      setError(err.message);
    }
  }

  if (!agent) return <div className="page"><div className="empty">{error || 'Loading companion…'}</div></div>;

  return (
    <div className="task">
      <header className="task-head">
        <Link to="/agents" className="task-back" aria-label="Back to agents">‹</Link>
        <Companion archetype={agent.archetype} color={agent.color}
                   state={agent.state} size={34} name={agent.name} />
        <div className="task-who">
          <strong>{agent.name}</strong>
          <span>{agent.role}</span>
        </div>
        <span className={`state-chip cc-tone-${(STATES[agent.state] || STATES.idle).tone}`}>
          <i className="cc-dot" aria-hidden="true" />
          {(STATES[agent.state] || STATES.idle).label}
        </span>
      </header>

      {error && <div className="empty"><strong>Message not sent</strong><span>{error}</span></div>}
      <Timeline items={items} streaming={null} agents={agents}
                approvals={[]} onDecide={() => {}} />

      <form className="composer" onSubmit={send}>
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                  placeholder={`Ask ${agent.name} for something…`}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(e); }
                  }} />
        <div className="send">
          <button className="primary" disabled={!draft.trim()}>Send</button>
          <span className="hint">⏎ send</span>
        </div>
      </form>
    </div>
  );
}
