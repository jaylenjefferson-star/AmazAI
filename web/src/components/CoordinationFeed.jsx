/**
 * Agent<->agent traffic bound to one thread: handoffs proposed on any run
 * this thread has driven, and the `message_agent` sends `collab.send` wrote
 * here. Read-only by construction — no composer is rendered for it,
 * anywhere it is used.
 *
 * This is the console's answer to "message-thread isolation is not the
 * same as a shared bus": an owner watching a room sees this feed, but
 * cannot post into it, because the owner was never one of its two parties.
 * To actually steer either agent, the copy below says exactly what to do
 * instead.
 */
import Handoff from './Handoff';
import AgentAvatar from './AgentAvatar';

function AgentMessageRow({ item, agents }) {
  const of = (id) => agents.find((a) => a.agentId === id);
  const name = (id) => of(id)?.name || id;
  const deferred = item.priority?.startsWith('Deferred');

  return (
    <div className="handoff enter">
      <div className="handoff-head">
        <span className="who-chip">
          <AgentAvatar shape={of(item.fromAgentId)?.archetype} color={of(item.fromAgentId)?.color}
                       size={16} name={name(item.fromAgentId)} />
          {name(item.fromAgentId)}
        </span>
        <span className="arrow" aria-hidden="true">→</span>
        <span className="who-chip to">
          <AgentAvatar shape={of(item.toAgentId)?.archetype} color={of(item.toAgentId)?.color}
                       size={16} name={name(item.toAgentId)} />
          {name(item.toAgentId)}
        </span>
        <span className={`status ${deferred ? '' : 'cc-tone-warn'}`}>{item.priority}</span>
      </div>
      {item.summary && <div className="goal">{item.summary}</div>}
    </div>
  );
}

export default function CoordinationFeed({ items = [], agents = [] }) {
  return (
    <div className="coordination">
      <p className="hint-text">
        Observed coordination — to steer, message an agent directly or create
        a room with both agents and you.
      </p>
      {items.length === 0 && (
        <div className="empty">No agent-to-agent activity here yet.</div>
      )}
      {items.map((item, i) => (
        item.kind === 'handoff'
          ? <Handoff key={i} handoff={item} agents={agents} />
          : <AgentMessageRow key={i} item={item} agents={agents} />
      ))}
    </div>
  );
}
