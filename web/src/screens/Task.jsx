import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import AgentProfile from '../components/AgentProfile';
import ChatHeader from '../components/ChatHeader';
import Composer from '../components/Composer';
import Problem from '../components/Problem';
import { ChatSkeleton } from '../components/Skeleton';
import SkillDialog from '../components/SkillDialog';
import Timeline from '../components/Timeline';
import ToolsSheet from '../components/ToolsSheet';
import WorkspaceSheet from '../components/WorkspaceSheet';
import { api } from '../api';
import { presentAgent, useAgents } from '../hooks/useAgents';
import { alwaysAllow } from '../lib/approvals';
import { download } from '../lib/download';
import { COPY, friendly } from '../lib/errors';
import { usePresence, useSteps } from '../presence';
import { threadsChanged } from '../threadsBus';
import { threadToItems } from '../threadItems';

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'expired', 'partial']);

// What the composer can run itself. Each one does a real thing through the same
// API a screen would use -- there is no command here that only looks like one.
const COMMANDS = [
  { key: 'remember', hint: 'Save a fact to this Bot’s memory' },
  { key: 'routine', hint: 'Set up a routine for this Bot' },
];

/**
 * One Bot, one thread.
 *
 * The approval card and the execution timeline were the strongest parts of
 * the previous three-panel console, and replacing that shell with a
 * mobile-first one would have orphaned both. They live here instead: a
 * focused view you open from the inbox, with the Bot's own pane beside it on a
 * wide screen.
 */
export default function Task() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const { agents } = useAgents();
  const [agent, setAgent] = useState(null);
  const [items, setItems] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [runId, setRunId] = useState(null);
  const [runState, setRunState] = useState(null);
  const [error, setError] = useState('');
  const [profileOpen, setProfileOpen] = useState(false);
  const [deskOpen, setDeskOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const composer = useRef(null);
  const sentTask = useRef(false);
  const location = useLocation();
  const [skillDraft, setSkillDraft] = useState(null);
  const [skillCatalog, setSkillCatalog] = useState([]);
  const pollRef = useRef(null);
  const threadId = `dm-${agentId}`;
  const live = usePresence()[agentId];
  const steps = useSteps(threadId);

  const loadAgent = useCallback(() => {
    api.agent(agentId).then((a) => setAgent(presentAgent(a))).catch((e) => setError(e.message));
  }, [agentId]);

  const loadThread = useCallback(() => {
    api.thread(threadId).then((thread) => {
      const next = threadToItems(thread.messages);
      setItems(next);
      // Opening a conversation is reading it. Marked after the messages are
      // in hand rather than on mount, so a thread whose load failed is not
      // recorded as seen. Failure here is silent on purpose.
      api.markRead(threadId).then(threadsChanged).catch(() => {});
    }).catch((e) => {
      // A newly provisioned agent has no conversation yet; a missing thread
      // is not a substitute for demo conversation history.
      if (!String(e.message).includes('404')) setError(e.message);
    });
  }, [threadId]);

  useEffect(() => { loadAgent(); loadThread(); }, [loadAgent, loadThread]);

  // A contact card's "Profile" arrives here with `profile` in navigation state.
  useEffect(() => {
    if (agent && location.state?.profile) {
      setProfileOpen(true);
      navigate(location.pathname, { replace: true, state: null });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);

  // "New task" hands the goal over in navigation state; it is sent once, as a
  // normal message, and the state is cleared so a refresh cannot send it again.
  useEffect(() => {
    const task = location.state?.task;
    if (!agent || !task || sentTask.current) return;
    sentTask.current = true;
    navigate(location.pathname, { replace: true, state: null });
    sendText(task).catch((e) => setError(e.message));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);
  useEffect(() => { api.skills().then((r) => setSkillCatalog(r.skills || [])).catch(() => {}); }, []);

  // Runs that are still in flight, so a run left mid-approval from an
  // earlier visit still shows a typing indicator and its approval card
  // rather than looking finished.
  useEffect(() => {
    api.approvals('pending').then((r) => {
      setPendingApprovals((r.approvals || []).filter((a) => a.requestedBy?.agentId === agentId));
    }).catch(() => {});
  }, [agentId]);

  const stopPolling = useCallback(() => {
    clearInterval(pollRef.current);
    pollRef.current = null;
    setRunState(null);
    setRunId(null);
  }, []);

  const pollRun = useCallback((id) => {
    clearInterval(pollRef.current);
    setRunId(id);
    pollRef.current = setInterval(async () => {
      try {
        const run = await api.run(id);
        // The control plane stores canonical state values as uppercase enum
        // strings (for example, `COMPLETED`), while this view uses lowercase
        // presentation states. Normalize at the boundary.
        const state = String(run.state || '').toLowerCase();
        setRunState(state);
        const runApprovals = (run.approvals || []).filter((a) => a.status === 'pending');
        setPendingApprovals((current) => {
          const others = current.filter((a) => a.runId !== id);
          return [...others, ...runApprovals];
        });
        if (TERMINAL_STATES.has(state)) {
          // A redirect ends one run and starts another. Follow it, rather than
          // showing the Bot as finished a moment before it starts again.
          if (state === 'cancelled' && run.redirectedTo) { pollRun(run.redirectedTo); loadThread(); return; }
          stopPolling();
          loadThread();
        } else if (runApprovals.length > 0) {
          loadThread();
        }
      } catch {
        stopPolling();
      }
    }, 1500);
  }, [loadThread, stopPolling]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  // The run to stop or redirect: the one this view started, or -- if the page
  // was opened mid-run -- the one the socket says the Bot is in.
  const stoppable = runId || live?.runId || pendingApprovals.find((a) => a.status === 'pending')?.runId || null;
  const busy = Boolean(stoppable) && (
    (runState && !TERMINAL_STATES.has(runState)) || Boolean(live && live.state !== 'complete'));

  // Text in, run out -- shared by the composer and by a tapped suggestion, so
  // a chip is exactly a message the operator typed and cannot behave
  // differently from one.
  async function sendText(text, { redirect = false } = {}) {
    if (!text || !agent) return;
    setError('');
    setItems((current) => [...current, { type: 'message', role: 'user', author: 'you', text, at: new Date().toISOString() }]);
    const result = await api.send(threadId, text, redirect && stoppable ? { redirectRunId: stoppable } : {});
    if (result.runId) pollRun(result.runId);
    if (redirect) loadThread();
  }

  async function stop() {
    if (!stoppable) return;
    setError('');
    try {
      await api.cancel(stoppable);
      setRunState('cancelling');
      pollRun(stoppable);
    } catch (err) {
      setError(err.message);
    }
  }

  async function command(key, arg) {
    if (key === 'remember') {
      if (!arg) throw new Error('Say what to remember: /remember <a fact about how you work>');
      await api.addMemory(agentId, { title: arg.slice(0, 60), body: arg, kind: 'foundational' });
      loadThread();
      loadAgent();
    } else if (key === 'routine') {
      navigate('/routines/new', { state: { prefill: { agentId, prompt: arg || undefined } } });
    }
  }

  function onAction(kind) {
    if (kind === 'tools') setToolsOpen(true);
    else if (kind === 'computer') setDeskOpen(true);
    else if (kind === 'routine') navigate('/routines/new', { state: { prefill: { agentId } } });
    else if (kind === 'task') composer.current?.insert('Task: ');
    else if (kind === 'artifact') composer.current?.insert('Create a document: ');
  }

  async function decide(approval, approve, note, opts) {
    await api.decide(approval.runId, approval.approvalId, approve, note);
    if (approve && opts?.always) await alwaysAllow(approval).catch((e) => setError(e.message));
    setPendingApprovals((current) => current.map((a) => (
      a.approvalId === approval.approvalId ? { ...a, status: approve ? 'approved' : 'denied' } : a
    )));
    // The decision resumes the run; watch it.
    if (approve && approval.runId) pollRun(approval.runId);
    loadThread();
    loadAgent();
    threadsChanged();
  }

  async function rememberMessage(item) {
    const first = String(item.text || '').split('\n')[0].slice(0, 60) || 'From a conversation';
    try {
      await api.addMemory(agentId, { title: first, body: item.text, kind: 'note' });
      loadThread();
      loadAgent();
    } catch (err) { setError(err.message); }
  }

  const timelineItems = useMemo(() => {
    const list = [...items];
    // Only a run still going shows the live trail. A finished run's trail is on
    // its message (persisted, above), so showing both would draw it twice.
    if (steps && !steps.endedAt) list.push({ type: 'steps', steps });
    return [...list, ...pendingApprovals.map((approval) => ({ type: 'approval', approval }))];
  }, [items, steps, pendingApprovals]);

  const cardCtx = useMemo(() => ({ agentId }), [agentId]);

  const typing = runState && !TERMINAL_STATES.has(runState) && pendingApprovals.every((a) => a.status !== 'pending')
    ? { name: agent?.name, verb: (STATES.thinking || STATES.working).verb }
    : null;

  // Who can be addressed with @, and which skills the `/` menu offers: only ones
  // this Bot really has -- assigned and active -- because the API checks the same.
  const mentionables = useMemo(() => agents
    .filter((a) => a.agentId !== agentId && !['offline', 'blocked'].includes(a.state))
    .map((a) => ({ id: a.agentId, name: a.name, archetype: a.archetype, color: a.color })), [agents, agentId]);
  const skills = useMemo(() => {
    const byId = Object.fromEntries(skillCatalog.map((s) => [s.skillId, s]));
    return (agent?.skillAssignments || [])
      .map((a) => byId[a.skillId])
      .filter((s) => s && s.status === 'active')
      .map((s) => ({ id: s.skillId, name: s.name, description: s.description }));
  }, [agent, skillCatalog]);

  // The state everyone else reads. The socket knows more than the poll -- it says
  // *what* the Bot is doing -- so it wins; a polled run with no live event yet is
  // still "thinking", and that is only the fallback.
  const shown = live?.state || (typing ? 'thinking' : agent?.state);

  const exportTranscript = useCallback(() => {
    const lines = [`# ${agent?.name || 'Conversation'}`, ''];
    for (const it of items) {
      if (it.type === 'message') lines.push(`**${it.role === 'user' ? 'You' : it.author || 'Bot'}:** ${it.text}`, '');
      else if (it.type === 'event') lines.push(`_${it.text}_`, '');
    }
    return lines.join('\n');
  }, [items, agent]);

  if (!agent) {
    return error
      ? <div className="chat"><Problem message={friendly(error, COPY.load)} onRetry={() => { setError(''); loadAgent(); loadThread(); }} /></div>
      : <div className="chat"><ChatSkeleton /></div>;
  }

  const stateInfo = STATES[shown] || STATES.idle;
  const status = shown && !['idle', 'complete'].includes(shown) ? (live?.action || stateInfo.label) : '';

  return (
    <div className="chat">
      <ChatHeader
        back="/"
        mark={<Companion archetype={agent.archetype} color={agent.color} state={shown} size={30} name={agent.name} />}
        name={agent.name}
        status={status}
        tone={stateInfo.tone}
        onOpen={() => setProfileOpen(true)}
        action={{ icon: 'computer', label: `Open ${agent.name}'s workspace`, onClick: () => setDeskOpen(true),
                  badge: Boolean(status) }}
      />

      {error && <Problem message={friendly(error, COPY.load)} onRetry={() => { setError(''); loadThread(); }} />}

      <Timeline items={timelineItems} streaming={null} typing={typing} agents={agents}
                approvals={pendingApprovals} onDecide={decide} onSuggest={(t) => sendText(t)}
                cardCtx={cardCtx} showAuthor={false}
                onSaveSkill={(item) => setSkillDraft({ text: item.text })}
                onRemember={rememberMessage} mentionIds={mentionables.map((m) => m.id)} />

      <Composer ref={composer} name={agent.name} mentionables={mentionables} skills={skills}
                commands={COMMANDS} busy={busy} onSend={sendText} onStop={stop} onCommand={command}
                placeholder={agent.entrypoint ? `Ask ${agent.name}` : `Message ${agent.name}`}
                onAction={onAction} />

      {profileOpen && (
        <AgentProfile agent={agent} agents={agents} threadId={threadId}
                      // A change writes a history line into this conversation; refresh both
                      // or the line exists on the server and is missing from the screen.
                      onChange={() => { loadAgent(); loadThread(); }}
                      onExport={() => download(exportTranscript(), `${agent.name}.md`)}
                      onClose={() => setProfileOpen(false)} />
      )}
      {deskOpen && (
        <WorkspaceSheet agent={agent} threadId={threadId} agents={agents} live={live}
                        steps={steps || items.filter((i) => i.type === 'steps').slice(-1)[0]?.steps}
                        approvals={pendingApprovals} onClose={() => setDeskOpen(false)} />
      )}
      {toolsOpen && <ToolsSheet onClose={() => { setToolsOpen(false); loadAgent(); }} />}

      {skillDraft && (
        <SkillDialog draft={skillDraft} threadId={threadId} agentId={agentId}
                     onClose={() => setSkillDraft(null)}
                     onSaved={() => { setSkillDraft(null); loadThread(); loadAgent(); }} />
      )}
    </div>
  );
}
