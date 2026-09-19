/**
 * An agent handing work to another agent.
 *
 * The backend already treats this as a first-class thing: `handoff` is an
 * inline function, and it writes a Handoff entity with a goal, the state so
 * far, and explicit constraints. It used to arrive here as an anonymous grey
 * tool chip, which made the single most interesting event in a multi-agent
 * org — one agent deciding another should take over — look like a file read.
 *
 * The line that matters most is the last one. `grantsOffered` is always empty
 * by construction: authority does not travel with delegated work, so the
 * receiving agent runs under its own grants and its own budget. Saying so on
 * the card means nobody has to remember it.
 */
export default function Handoff({ handoff, agents = [] }) {
  const name = (id) => agents.find((a) => a.agentId === id)?.name || id;
  const constraints = handoff.constraints || [];

  return (
    <div className="handoff enter">
      <div className="handoff-head">
        <span className="who-chip">{name(handoff.fromAgentId)}</span>
        <span className="arrow" aria-hidden="true">→</span>
        <span className="who-chip to">{name(handoff.toAgentId)}</span>
        <span className="status">{handoff.status || 'proposed'}</span>
      </div>

      {handoff.goal && <div className="goal">{handoff.goal}</div>}

      {constraints.length > 0 && (
        <ul className="constraints">
          {constraints.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}

      <div className="note">
        Runs under {name(handoff.toAgentId)}&rsquo;s own grants and budget — no
        access was passed along.
      </div>
    </div>
  );
}

/**
 * Consecutive agent-to-agent traffic, folded into one line.
 *
 * Without this, a Chief of Staff fanning work out to six agents buries the
 * part you actually care about under six near-identical cards.
 */
export function CollabSummary({ handoffs, agents = [], onExpand }) {
  const name = (id) => agents.find((a) => a.agentId === id)?.name || id;
  const parties = [...new Set(handoffs.flatMap((h) => [h.fromAgentId, h.toAgentId]))];
  const shown = parties.slice(0, 4);

  return (
    <button className="collab" onClick={onExpand}>
      <span className="faces" aria-hidden="true">
        {shown.map((id) => (
          <span key={id} className="face" title={name(id)}>{name(id).slice(0, 1)}</span>
        ))}
      </span>
      <span className="text">
        {handoffs.length} {handoffs.length === 1 ? 'handoff' : 'handoffs'} between{' '}
        {parties.length} agents
      </span>
      <span className="caret" aria-hidden="true">▸</span>
    </button>
  );
}
