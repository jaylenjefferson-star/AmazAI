import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import ChatHeader from '../components/ChatHeader';
import GroupMark from '../components/GroupMark';
import Composer from '../components/Composer';
import CoordinationFeed from '../components/CoordinationFeed';
import Problem from '../components/Problem';
import RoomInfo from '../components/RoomInfo';
import Sheet from '../components/Sheet';
import { ChatSkeleton } from '../components/Skeleton';
import Timeline from '../components/Timeline';
import ToolsSheet from '../components/ToolsSheet';
import { api } from '../api';
import { useAgents } from '../hooks/useAgents';
import { alwaysAllow } from '../lib/approvals';
import { COPY, friendly } from '../lib/errors';
import { usePresence } from '../presence';
import { threadsChanged } from '../threadsBus';
import { localMessage, mergeCoordination, reconcileOptimistic, threadToItems } from '../threadItems';

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'expired', 'partial']);

/**
 * A task-bound channel: several Bots, one thread, an owner of record.
 *
 * Two tabs, not one feed, on purpose: the message history is something the
 * owner posts into, and the coordination feed is agents talking to each
 * other about this same task. Merging them would make it look like the
 * owner is a party to hop counts and priority wakes that were never
 * addressed to them.
 *
 * `@bot` in the composer wakes exactly the Bots named, in parallel, each on its
 * own run; a message that names no one goes to the lead. The transcript says who
 * was woken -- a line the API writes, not one drawn here.
 */
export default function Room() {
  const { roomId } = useParams();
  const { agents } = useAgents();
  const [thread, setThread] = useState(null);
  const [items, setItems] = useState([]);
  const [coordination, setCoordination] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [running, setRunning] = useState({});            // runId -> agentId
  const [error, setError] = useState('');
  const [infoOpen, setInfoOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const composer = useRef(null);
  const navigate = useNavigate();
  const presence = usePresence();
  const pollers = useRef({});

  const loadThread = useCallback(() => {
    return api.thread(roomId).then((t) => {
      setThread(t);
      setItems((prev) => reconcileOptimistic(prev, threadToItems(t.messages)));
      // Same rule as a Bot conversation: reading it is what marks it read, and
      // only once the messages actually arrived.
      api.markRead(roomId).then(threadsChanged).catch(() => {});
    }).catch((e) => setError(e.message));
  }, [roomId]);

  useEffect(() => { loadThread(); }, [loadThread]);
  useEffect(() => {
    api.coordination(roomId).then((r) => setCoordination(r.coordination || [])).catch(() => {});
  }, [roomId, items.length]);
  useEffect(() => () => Object.values(pollers.current).forEach(clearInterval), []);

  const members = useMemo(
    () => (thread?.agentIds || []).map((id) => agents.find((a) => a.agentId === id)).filter(Boolean),
    [thread, agents],
  );

  function watch(runId, agentId) {
    setRunning((r) => ({ ...r, [runId]: agentId }));
    clearInterval(pollers.current[runId]);
    pollers.current[runId] = setInterval(async () => {
      try {
        const run = await api.run(runId);
        const state = String(run.state || '').toLowerCase();
        const pending = (run.approvals || []).filter((a) => a.status === 'pending');
        setPendingApprovals((cur) => [...cur.filter((a) => a.runId !== runId), ...pending]);
        if (TERMINAL_STATES.has(state)) {
          clearInterval(pollers.current[runId]);
          setRunning((r) => { const { [runId]: _done, ...rest } = r; return rest; });
          loadThread();
        }
      } catch {
        clearInterval(pollers.current[runId]);
        setRunning((r) => { const { [runId]: _done, ...rest } = r; return rest; });
      }
    }, 1500);
  }

  async function send(text) {
    setItems((current) => [...current, localMessage(text)]);
    const result = await api.send(roomId, text);
    (result.runs || [{ runId: result.runId, agentId: null }]).forEach((r) => r.runId && watch(r.runId, r.agentId));
    // The "Woke X and Y" line is written by the API; fetch it.
    loadThread();
  }

  async function stopAll() {
    setError('');
    try {
      await Promise.all(Object.keys(running).map((id) => api.cancel(id)));
    } catch (err) { setError(err.message); }
  }

  async function decide(approval, approve, note, opts) {
    await api.decide(approval.runId, approval.approvalId, approve, note);
    if (approve && opts?.always) await alwaysAllow(approval).catch((e) => setError(e.message));
    setPendingApprovals((cur) => cur.map((a) => (
      a.approvalId === approval.approvalId ? { ...a, status: approve ? 'approved' : 'denied' } : a
    )));
    if (approve) watch(approval.runId, approval.requestedBy?.agentId);
    loadThread();
  }

  // The work between agents belongs in the conversation, in the order it happened:
  // a handoff is a delegation object, a message between agents is a quiet line.
  // Neither is addressed to you, and neither is a chat bubble, so the owner is
  // never made to look like a party to it. Events carry no time, so they keep
  // their place; only timestamped rows are compared.
  const timelineItems = useMemo(() => [
    ...mergeCoordination(items, coordination),
    ...pendingApprovals.map((approval) => ({ type: 'approval', key: `approval:${approval.approvalId}`, approval })),
  ], [items, coordination, pendingApprovals]);

  const mentionables = useMemo(
    () => members.map((a) => ({ id: a.agentId, name: a.name, archetype: a.archetype, color: a.color })),
    [members],
  );

  // A room is task-bound, so it ends. When it has, the composer goes rather
  // than sitting there disabled: a greyed-out box invites a click and then
  // explains nothing, and the room is still worth reading.
  const statusLabel = thread?.status && thread.status !== 'active' ? thread.status : 'active';
  const readOnly = Boolean(thread?.readOnly) || statusLabel !== 'active';

  if (!thread) {
    return error
      ? <div className="chat"><Problem message={friendly(error, COPY.load)} onRetry={() => { setError(''); loadThread(); }} /></div>
      : <div className="chat"><ChatSkeleton /></div>;
  }

  // Who is doing what, said in one line under the room's name.
  const active = members.filter((m) => ['thinking', 'working', 'waiting'].includes(presence[m.agentId]?.state));
  const askingYou = pendingApprovals.some((a) => a.status === 'pending');
  const status = readOnly ? `${statusLabel} · read-only`
    : askingYou ? 'Waiting on you'
    : active.length === 1 ? `${active[0].name} is working`
    : active.length > 1 ? `${active.length} agents working`
    : Object.keys(running).length ? 'Working…' : '';

  const mark = (
    <GroupMark members={members} size={34}
               states={Object.fromEntries(members.map((m) => [m.agentId, presence[m.agentId]?.state || m.state]))} />
  );

  function onAction(kind) {
    if (kind === 'tools') setToolsOpen(true);
    else if (kind === 'task') composer.current?.insert('@');
    else if (kind === 'artifact') composer.current?.insert('Create a document: ');
  }

  return (
    <div className="chat">
      <ChatHeader
        back="/"
        mark={mark}
        name={thread.title || 'Room'}
        status={status || members.map((a) => a.name).join(', ')}
        tone={askingYou ? 'warn' : active.length ? 'accent' : 'neutral'}
        onOpen={() => setInfoOpen(true)}
        action={{ icon: 'layers', label: 'Coordination between agents', onClick: () => setActivityOpen(true),
                  badge: active.length > 1 }}
      />

      {error && <Problem message={friendly(error, COPY.load)} onRetry={() => { setError(''); loadThread(); }} />}

      <Timeline items={timelineItems} streaming={null} typing={null} agents={agents}
                approvals={pendingApprovals} onDecide={decide}
                mentionIds={mentionables.map((m) => m.id)} />

      {readOnly ? (
        <div className="room-closed" role="status">
          <strong>This room is {statusLabel}</strong>
          <span>Its history stays readable, and the runs it produced keep their sealed
            evidence. Start a new room to carry the work on.</span>
        </div>
      ) : (
        <Composer ref={composer} name={thread.title || 'the room'} mentionables={mentionables}
                  busy={Object.keys(running).length > 0} canRedirect={false}
                  placeholder="Message the room" actions={['upload', 'photo', 'camera', 'tools', 'task', 'artifact']}
                  onAction={onAction} onSend={send} onStop={stopAll} />
      )}

      {infoOpen && (
        <RoomInfo thread={thread} agents={agents} activityCount={coordination.length}
                  onActivity={() => setActivityOpen(true)} onClose={() => setInfoOpen(false)} onChanged={loadThread} />
      )}
      {activityOpen && (
        <Sheet title="Between agents" tall onClose={() => setActivityOpen(false)}>
          <CoordinationFeed items={coordination} agents={agents} />
        </Sheet>
      )}
      {toolsOpen && <ToolsSheet onClose={() => setToolsOpen(false)} />}
    </div>
  );
}
