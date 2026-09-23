import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import GroupMark from '../components/GroupMark';
import Problem from '../components/Problem';
import { RosterSkeleton } from '../components/Skeleton';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';
import { friendly } from '../lib/errors';
import { previewOf } from './Inbox';

function timeAgo(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.round(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

/**
 * Where Bots talk to each other -- on their own, off your main list.
 *
 * A room used to be a row in the same inbox as your own conversations,
 * ordered by whichever spoke last: a burst of bot-to-bot chatter about a
 * task could outrank a Bot actually waiting on you. Rooms are real work
 * happening (see docs/architecture/16), and read-only observation of them
 * is a deliberate feature -- but observing is something you opt into, not
 * something the app defaults you into. This is that opt-in: a reference
 * list, reachable from the profile menu, never surfaced unasked.
 */
export default function Rooms() {
  const { agents } = useAgents();
  const [threads, setThreads] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.threads().then((r) => { setThreads(r.threads || []); setError(''); })
      .catch((e) => setError(e.message));
  }, []);

  const byId = useMemo(() => new Map(agents.map((a) => [a.agentId, a])), [agents]);
  const rooms = useMemo(() => (threads || [])
    .filter((t) => t.kind === 'room')
    .sort((a, b) => String(b.lastActivity || '').localeCompare(String(a.lastActivity || ''))),
    [threads]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Rooms</h1>
          <p>Where your Bots coordinate with each other. Read-only — approvals and results still come to you directly.</p>
        </div>
      </header>

      {threads === null && !error && <RosterSkeleton rows={3} />}
      {error && <Problem message={friendly(error, "Couldn't load rooms.")} />}
      {threads !== null && !error && rooms.length === 0 && (
        <div className="empty">
          <strong>No rooms yet</strong>
          <span>A room opens when two or more Bots need to coordinate on the same task.</span>
        </div>
      )}

      <div className="row-list">
        {rooms.map((room) => {
          const members = (room.agentIds || []).map((id) => byId.get(id)).filter(Boolean);
          return (
            <Link key={room.threadId} to={`/rooms/${room.threadId}`} className="row-card">
              <GroupMark members={members} size={38} />
              <div className="row-body">
                <strong>{room.title || 'Room'}</strong>
                <span>
                  {members.length ? members.map((m) => m.name).join(', ') : 'No Bots in this room yet'}
                  {room.lastActivity ? ` · ${timeAgo(room.lastActivity)}` : ''}
                </span>
                {previewOf(room) && <span className="row-preview">{previewOf(room)}</span>}
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
