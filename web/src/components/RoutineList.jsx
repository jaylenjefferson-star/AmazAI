import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { describeSchedule } from '../schedules';
import Icon from './Icon';

/**
 * One Bot's routines, in its info pane.
 *
 * The same rows the Routines screen shows, filtered to this Bot -- because the
 * question a person has beside a conversation is "what does this one do on its
 * own?", and answering it should not mean leaving the conversation. Pausing is
 * the one action offered here; creating and archiving stay on the full screen,
 * where there is room for a schedule to be read before it is saved.
 */
export default function RoutineList({ agent }) {
  const [routines, setRoutines] = useState(null);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [started, setStarted] = useState({});   // routineId -> 'just now'

  useEffect(() => {
    let live = true;
    api.routines()
      .then((r) => { if (live) setRoutines((r.routines || []).filter(
        (x) => x.agentId === agent.agentId && x.status !== 'archived')); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [agent.agentId]);

  async function toggle(routine) {
    setBusyId(routine.routineId);
    setError('');
    try {
      const updated = await api.updateRoutine(routine.routineId, { enabled: !routine.enabled });
      setRoutines((rs) => rs.map((r) => (r.routineId === updated.routineId ? updated : r)));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId('');
    }
  }

  // One key per press, so a double-click is one run (the API dedupes on it).
  async function runNow(routine) {
    setBusyId(routine.routineId);
    setError('');
    try {
      const r = await api.runRoutine(routine.routineId, `${Date.now().toString(36)}`);
      setStarted((s) => ({ ...s, [routine.routineId]: r.deduplicated ? 'already started' : 'started just now' }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId('');
    }
  }

  if (error && !routines) return <div className="err"><span className="msg-text">{error}</span></div>;
  if (!routines) return <div className="empty">Loading…</div>;

  return (
    <>
      {error && <div className="err" style={{ marginBottom: 10 }}><span className="msg-text">{error}</span></div>}
      {routines.length === 0 && (
        <div className="empty" style={{ padding: '8px 0 14px' }}>
          <span className="title">No routines yet</span>
          <span>Ask {agent.name} to do something every morning, or set one up yourself.</span>
        </div>
      )}
      <ul className="routine-list">
        {routines.map((r) => (
          <li key={r.routineId} className="routine-row" data-paused={r.enabled ? undefined : 'true'}>
            <span className="routine-icon" aria-hidden="true">
              <Icon name="clock" size={18} />
            </span>
            <span className="routine-text">
              <strong>{r.name}</strong>
              <small>{started[r.routineId] || describeSchedule(r.trigger)}</small>
            </span>
            <button type="button" className="ghost sm icon-btn run-now"
                    aria-label={`Run ${r.name} now`} title="Run now"
                    disabled={busyId === r.routineId} onClick={() => runNow(r)}>
              <Icon name="play" size={14} />
            </button>
            <button type="button" role="switch" aria-checked={Boolean(r.enabled)}
                    aria-label={`${r.enabled ? 'Pause' : 'Resume'} ${r.name}`}
                    className="switch" disabled={busyId === r.routineId}
                    onClick={() => toggle(r)}>
              <span>{r.enabled ? 'Active' : 'Paused'}</span>
            </button>
          </li>
        ))}
      </ul>
      <Link className="btn-link" to="/routines/new"
            state={{ prefill: { agentId: agent.agentId } }}>New routine</Link>
    </>
  );
}
