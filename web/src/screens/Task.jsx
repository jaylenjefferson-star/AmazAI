import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import Composer from '../components/Composer';
import Icon from '../components/Icon';
import RightPanel from '../components/RightPanel';
import SkillDialog from '../components/SkillDialog';
import Timeline from '../components/Timeline';
import { api } from '../api';
import { presentAgent, useAgents } from '../hooks/useAgents';
import { usePins } from '../hooks/usePins';
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
  const [panelOpen, setPanelOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [skillDraft, setSkillDraft] = useState(null);
  const [skillCatalog, setSkillCatalog] = useState([]);
  const pollRef = useRef(null);
  const threadId = `dm-${agentId}`;
  const { pins, toggle: togglePin } = usePins();
  const pinned = pins.includes(threadId);
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

  async function decide(approval, approve, note) {
    await api.decide(approval.runId, approval.approvalId, approve, note);
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

  if (!agent) return <div className="page"><div className="empty">{error || 'Loading Bot…'}</div></div>;

  return (
    <div className="task">
      <div className="task-main">
      {/* Back goes to the inbox, which is where this conversation was opened
          from now that the inbox is home -- `/agents` was the old section
          list and returning there loses the thread you came in on.

          The identity sits centred between two equal-width controls rather
          than left-aligned beside them, so it stays centred whatever the
          name's length, and the status reads as the companion's own rather
          than as a chip parked at the end of a row. */}
      <header className="chat-head">
        <Link to="/" className="chat-icon" aria-label="Back to inbox">
          <Icon name="chevronLeft" size={20} />
        </Link>

        <div className="chat-identity">
          <Companion archetype={agent.archetype} color={agent.color}
                     state={shown} size={30} name={agent.name} />
          <span className="chat-who">
            <strong>{agent.name}</strong>
            {/* The same word the inbox row uses, from the same store, so the
                header and the list can never disagree about what a Bot is
                doing -- plus the line of detail the list shows on hover. */}
            <small className={`cc-tone-${(STATES[shown] || STATES.idle).tone}`}>
              {(STATES[shown] || STATES.idle).label}
              {live?.action ? ` — ${live.action}` : ''}
            </small>
          </span>
        </div>

        <button type="button" className="chat-icon" onClick={() => setMenuOpen(true)}
                aria-label={`More about ${agent.name}`} aria-haspopup="menu">
          <Icon name="more" size={20} />
        </button>
      </header>

      {error && <div className="empty"><strong>Something went wrong</strong><span>{error}</span></div>}
      <Timeline items={timelineItems} streaming={null} typing={typing} agents={agents}
                approvals={pendingApprovals} onDecide={decide} onSuggest={(t) => sendText(t)}
                cardCtx={cardCtx} showAuthor={false}
                onSaveSkill={(item) => setSkillDraft({ text: item.text })}
                onRemember={rememberMessage} mentionIds={mentionables.map((m) => m.id)} />

      <Composer name={agent.name} mentionables={mentionables} skills={skills}
                commands={COMMANDS} busy={busy} onSend={sendText} onStop={stop}
                onCommand={command} />
      </div>

      {/* The overflow: the things a conversation leads to. Settings is a screen
          because it is long; the pane stays a panel because it is read beside
          the conversation, not instead of it. */}
      {menuOpen && (
        <>
          <div className="scrim" onClick={() => setMenuOpen(false)} />
          <div className="sheet" role="menu" aria-label={`${agent.name} options`}>
            <h2 className="sheet-title">{agent.name}</h2>
            <Link className="sheet-row" role="menuitem" to={`/agents/${agentId}/settings`}>
              <Companion archetype={agent.archetype} color={agent.color} state="idle" size={30} />
              <span>
                <strong>Bot settings</strong>
                <small>Identity, instructions, model, budget and hours.</small>
              </span>
            </Link>
            <button type="button" className="sheet-row" role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      // Optimistic in the hook; a refusal (the twelve-pin
                      // ceiling) is worth saying, so it lands in the same
                      // banner a failed send uses.
                      togglePin(threadId).catch((e) => setError(e.message));
                    }}>
              <Icon name="pin" size={26} />
              <span>
                <strong>{pinned ? 'Unpin from top' : 'Pin to top'}</strong>
                <small>{pinned
                  ? 'Remove it from the strip above your inbox.'
                  : 'Keep it one tap away, above your inbox.'}</small>
              </span>
            </button>
            <button type="button" className="sheet-row" role="menuitem"
                    onClick={() => { setMenuOpen(false); setPanelOpen(true); }}>
              <Icon name="layers" size={26} />
              <span>
                <strong>Routines, memory and activity</strong>
                <small>What it does on its own, what it knows, what it has spent.</small>
              </span>
            </button>
          </div>
        </>
      )}

      <RightPanel threadId={threadId} agent={agent} agents={agents}
                  // A pane action (a memory saved or corrected, a routine run) writes a
                  // history line into this conversation; refresh both, or the line
                  // exists on the server and is missing from the screen.
                  onRefreshAgent={() => { loadAgent(); loadThread(); }} open={panelOpen}
                  onClose={() => setPanelOpen(false)} onExport={exportTranscript} />

      {skillDraft && (
        <SkillDialog draft={skillDraft} threadId={threadId} agentId={agentId}
                     onClose={() => setSkillDraft(null)}
                     onSaved={() => { setSkillDraft(null); loadThread(); loadAgent(); }} />
      )}
    </div>
  );
}
