import { STATES } from '../characters/Companion';
import AgentAvatar from './AgentAvatar';

/**
 * A room's coordination state, derived from durable run/task/handoff/artifact
 * rows -- never from a live token stream.
 *
 * A room deliberately shows no per-agent live deltas (a `delta` carries a runId
 * but no agentId, so with several Bots writing at once there is no honest way
 * to say whose words are whose -- see the note in Room.jsx). This view answers
 * the questions a delta stream cannot: who owns the current stage, what each
 * member is doing (working/waiting/needs-approval), whether a handoff has
 * happened here, and whether the team has produced a deliverable. Every line is
 * a projection of the `room` object `GET /threads/{id}/coordination` derives
 * with `presence.room_coordination`, using the same canonical activity
 * vocabulary (`Companion.STATES`) the rest of the console reads, so a member
 * reads identically here and on its own row.
 */

/** The one member-state bucket the backend spells `needs_approval`; the
 *  character system spells the same bucket `approval`. Mapped so the label and
 *  colour match everywhere. Every other bucket is shared verbatim. */
const STATE_ALIAS = { needs_approval: 'approval' };

/** The plain phrase for a member: the durable action line the backend derived
 *  when there is one, else the coarse canonical verb for the state. */
export function memberLine(member) {
  const state = STATE_ALIAS[member.state] || member.state;
  if (member.action) return member.action;
  return STATES[state]?.verb || '';
}

/** A one-line summary of the room's coordination state, for a header or a
 *  screen reader -- the strongest single fact first. */
export function coordinationSummary(room, nameOf) {
  if (!room) return '';
  if (room.needsApproval) return 'Waiting for your approval';
  if (room.stageOwnerAgentId) return `${nameOf(room.stageOwnerAgentId)} is working`;
  if (room.waiting) return 'Waiting on a teammate';
  if (room.artifactProduced) return 'Delivered an artifact';
  return '';
}

export default function RoomCoordination({ room, agents = [] }) {
  if (!room || !(room.members || []).length) return null;
  const of = (id) => agents.find((a) => a.agentId === id);
  const nameOf = (id) => of(id)?.name || id;

  return (
    <div className="room-coord">
      <div className="room-coord-members">
        {room.members.map((m) => {
          const state = STATE_ALIAS[m.state] || m.state;
          const owner = m.agentId === room.stageOwnerAgentId;
          return (
            <div key={m.agentId} className="room-coord-member" data-state={state}
                 data-owner={owner ? 'true' : undefined}>
              <AgentAvatar shape={of(m.agentId)?.archetype} color={of(m.agentId)?.color}
                           size={22} name={nameOf(m.agentId)} />
              <span className="room-coord-body">
                <strong>{nameOf(m.agentId)}</strong>
                <span className="room-coord-line">{memberLine(m)}</span>
              </span>
              {owner && <span className="room-coord-tag" title="Owns the current stage">On it</span>}
            </div>
          );
        })}
      </div>

      {/* Room-level facts a per-agent line cannot carry: that the team
          coordinated (a handoff) or produced something (an artifact). Both are
          read straight off durable rows, so they persist across a reload. */}
      {(room.handoffOccurred || room.artifactProduced) && (
        <div className="room-coord-flags">
          {room.handoffOccurred && <span className="room-coord-flag">Work handed off</span>}
          {room.artifactProduced && (
            <span className="room-coord-flag room-coord-flag--deliver">Artifact produced</span>
          )}
        </div>
      )}
    </div>
  );
}
