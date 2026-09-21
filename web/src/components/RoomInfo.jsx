import { useState } from 'react';
import { api } from '../api';
import Companion from '../characters/Companion';
import { friendly } from '../lib/errors';
import { ContactChip, useContacts } from './ContactCard';
import Icon from './Icon';
import Problem from './Problem';
import Sheet from './Sheet';

// Mirrors `collab.MAX_ROOM_MEMBERS`; the API is the authority.
const MAX_MEMBERS = 6;

/**
 * Who is in a room, and who can be added.
 *
 * Every change is a real `PATCH /threads/{id}`: the API applies the same cap and
 * the same "must be a real, seated agent" check creating a room does, and writes a
 * "joined" or "left" line into the conversation. Tapping a member opens their
 * contact card.
 */
export default function RoomInfo({ thread, agents, activityCount = 0, onActivity, onClose, onChanged }) {
  const { open } = useContacts();
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const ids = thread.agentIds || [];
  const members = ids.map((id) => agents.find((a) => a.agentId === id)).filter(Boolean);
  const addable = agents.filter((a) => !ids.includes(a.agentId) && a.state !== 'offline');
  const closed = Boolean(thread.readOnly) || (thread.status && thread.status !== 'active');

  async function change(next) {
    setBusy(true);
    setProblem(null);
    try {
      await api.patchThread(thread.threadId, { agentIds: next });
      await onChanged();
    } catch (err) {
      setProblem({ error: err, message: friendly(err, "Couldn't change who is in the room. Nothing was changed.") });
    } finally { setBusy(false); }
  }

  return (
    <Sheet title={thread.title || 'Room'} tall onClose={onClose}>
      <div className="ri-stack" aria-hidden="true">
        {members.slice(0, 4).map((a) => <Companion key={a.agentId} archetype={a.archetype} color={a.color} state={a.state} size={44} />)}
      </div>
      <p className="ri-count">{ids.length} of {MAX_MEMBERS} agents{closed ? ` · ${thread.status || 'closed'}` : ''}</p>

      {problem && <Problem message={problem.message} error={problem.error} inline />}

      <div className="sx-group">
        {members.map((a) => (
          <div className="ri-row" key={a.agentId}>
            <button type="button" className="ri-who" onClick={() => open(a.agentId)}>
              <Companion archetype={a.archetype} color={a.color} state={a.state} size={38} name={a.name} />
              <span className="sx-text"><strong>{a.name}</strong><small>{[a.title, a.role].filter(Boolean).join(' · ') || `@${a.agentId}`}</small></span>
            </button>
            {!closed && (
              <button type="button" className="pf-x" aria-label={`Remove ${a.name}`} disabled={busy || ids.length <= 1}
                      title={ids.length <= 1 ? 'A room needs at least one agent' : undefined}
                      onClick={() => change(ids.filter((x) => x !== a.agentId))}><Icon name="x" size={15} /></button>
            )}
          </div>
        ))}
      </div>

      {!closed && ids.length < MAX_MEMBERS && (
        <div className="ri-add">
          <span className="cm-label">Add to the room</span>
          {addable.length === 0 && <p className="pf-hint">Everyone is already here.</p>}
          <div className="cm-picks">
            {addable.map((a) => (
              <button key={a.agentId} type="button" className="cm-pick" disabled={busy} onClick={() => change([...ids, a.agentId])}>
                <Companion archetype={a.archetype} color={a.color} state="idle" size={22} /><span>{a.name}</span><Icon name="plus" size={14} />
              </button>
            ))}
          </div>
        </div>
      )}
      {!closed && ids.length >= MAX_MEMBERS && <p className="pf-hint">A room holds at most {MAX_MEMBERS} agents.</p>}

      {onActivity && (
        <div className="sx-group">
          <button type="button" className="sx-row" onClick={() => { onClose(); onActivity(); }}>
            <span className="sx-icon"><Icon name="layers" size={20} /></span>
            <span className="sx-text"><strong>Coordination</strong>
              <small>{activityCount ? `${activityCount} handoff${activityCount === 1 ? '' : 's'} and message${activityCount === 1 ? '' : 's'} between agents` : 'Nothing between agents yet'}</small></span>
            <Icon name="forward" size={16} />
          </button>
        </div>
      )}
    </Sheet>
  );
}
