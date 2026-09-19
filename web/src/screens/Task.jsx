import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import Timeline from '../components/Timeline';
import { fixtureAgents } from '../fixtures';

/**
 * One companion, one thread.
 *
 * The approval card and the execution timeline were the strongest parts of
 * the previous three-panel console, and replacing that shell with a
 * mobile-first one would have orphaned both. They live here instead: a
 * focused view you open from Agents, rather than a third panel that a phone
 * has no room for.
 */
export default function Task() {
  const { agentId } = useParams();
  const agents = fixtureAgents();
  const agent = agents.find((a) => a.agentId === agentId) || agents[0];
  const [draft, setDraft] = useState('');

  const items = useMemo(() => ([
    { type: 'message', role: 'user', author: 'you',
      text: 'Build the console and push it to the CloudFront distribution. Tell me before anything touches production.' },
    { type: 'message', role: 'assistant', author: agent.name,
      text: 'Building web/ now. I will need approval before the invalidation, since that is user-visible immediately.' },
    { type: 'tool', name: 'shell', summary: 'npm run build  →  built in 4.21s' },
    { type: 'handoff', handoff: {
      handoffId: 'hoff_31ab', fromAgentId: agent.agentId, toAgentId: 'ops',
      status: 'proposed',
      goal: 'Confirm the distribution is serving the new bundle once the invalidation clears.',
      constraints: ['Read-only: no stack changes', 'Stop and report if the 5xx rate moves'],
      grantsOffered: [],
    } },
    { type: 'message', role: 'assistant', author: agent.name,
      text: 'Build is clean and the bundle is on S3. The last step is an invalidation — immediate and irreversible, so it needs you.' },
    { type: 'approval', approval: {
      approvalId: 'apv-7c41', runId: 'run-9a22', agentId: agent.agentId,
      action: 'cloudfront.create_invalidation',
      risk: 'high', reversible: false, status: 'pending',
      requestedAt: new Date(Date.now() - 90_000).toISOString(),
      expiresAt: new Date(Date.now() + 8 * 60_000).toISOString(),
      arguments: { distributionId: 'EXAMPLE00000001', paths: '/*' },
      target: { account: '123456789012', env: 'production', region: 'us-west-2' },
      why: 'The console bundle hash changed, so cached index.html would keep serving the previous build.',
      requestedBy: { agentId: agent.agentId },
    } },
  ]), [agent]);

  const [approvals, setApprovals] = useState(
    () => items.filter((i) => i.type === 'approval').map((i) => i.approval));

  function decide(approval, approve, note) {
    setApprovals((a) => a.map((x) => x.approvalId === approval.approvalId
      ? { ...x, status: approve ? 'approved' : 'denied', note } : x));
  }

  return (
    <div className="task">
      <header className="task-head">
        <Link to="/agents" className="task-back" aria-label="Back to agents">‹</Link>
        <Companion archetype={agent.archetype} color={agent.color}
                   state={agent.state} size={34} name={agent.name} />
        <div className="task-who">
          <strong>{agent.name}</strong>
          <span>{agent.role}</span>
        </div>
        <span className={`state-chip cc-tone-${STATES[agent.state].tone}`}>
          <i className="cc-dot" aria-hidden="true" />
          {STATES[agent.state].label}
        </span>
      </header>

      <Timeline items={items} streaming={null} agents={agents}
                approvals={approvals} onDecide={decide} />

      <form className="composer" onSubmit={(e) => { e.preventDefault(); setDraft(''); }}>
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                  placeholder={`Ask ${agent.name} for something…`}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); setDraft(''); }
                  }} />
        <div className="send">
          <button className="primary" disabled={!draft.trim()}>Send</button>
          <span className="hint">⏎ send</span>
        </div>
      </form>
    </div>
  );
}
