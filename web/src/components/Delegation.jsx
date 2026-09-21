import { useState } from 'react';
import Companion from '../characters/Companion';
import Handoff from './Handoff';
import Icon from './Icon';

/**
 * One agent handing work to another, as a thing in the conversation.
 *
 * It says who gave it to whom and where it stands, in a sentence, with both
 * avatars. Tap it and the delegated work opens right here (the goal, what was
 * handed over, what did not travel with it) without leaving the thread.
 *
 * Authority never travels with delegated work: the receiver runs under its own
 * grants and budget. The expanded view says so.
 */
function statusLine(status, to) {
  switch ((status || 'proposed').toLowerCase()) {
    case 'accepted':
    case 'in_progress':
    case 'working': return `${to} is working on it.`;
    case 'completed':
    case 'done': return `${to} finished a task.`;
    case 'declined':
    case 'rejected': return `${to} passed on it.`;
    case 'failed': return `${to} couldn't finish it.`;
    default: return `Waiting for ${to} to pick it up.`;
  }
}

export default function Delegation({ handoff, agents = [] }) {
  const [open, setOpen] = useState(false);
  const find = (id) => agents.find((a) => a.agentId === id);
  const from = find(handoff.fromAgentId);
  const to = find(handoff.toAgentId);
  const fromName = from?.name || handoff.fromAgentId;
  const toName = to?.name || handoff.toAgentId;
  const live = ['accepted', 'in_progress', 'working'].includes(String(handoff.status || '').toLowerCase());

  return (
    <div className="dg">
      <button type="button" className={`dg-pill${live ? ' is-live' : ''}`} onClick={() => setOpen((o) => !o)}
              aria-expanded={open}>
        <span className="dg-faces" aria-hidden="true">
          <Companion archetype={from?.archetype || 'pebble'} color={from?.color || '#7b93ff'} state="idle" size={26} />
          <Icon name="arrowright" size={15} />
          <Companion archetype={to?.archetype || 'pebble'} color={to?.color || '#7b93ff'}
                     state={live ? 'working' : 'idle'} size={26} />
        </span>
        <span className="dg-text">
          <strong>{fromName} assigned this to {toName}</strong>
          <small>{statusLine(handoff.status, toName)}</small>
        </span>
        <Icon name={open ? 'x' : 'forward'} size={16} />
      </button>
      {open && <div className="dg-detail"><Handoff handoff={handoff} agents={agents} /></div>}
    </div>
  );
}
