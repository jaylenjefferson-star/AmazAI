import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { STATES } from '../characters/Companion';
import { Activity, Computer } from './RightPanel';
import Icon from './Icon';
import Problem from './Problem';
import Sheet from './Sheet';

const TABS = [['now', 'Now'], ['files', 'Files'], ['activity', 'Activity'], ['computer', 'Computer']];

const ago = (iso) => {
  if (!iso) return '';
  const s = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
};

const VERDICT = { allowed: 'Ran', asked: 'Asked', denied: 'Stopped' };

/**
 * An agent's desk: what it is doing now, what it has produced, what it has done,
 * and the terminal it works in.
 *
 * Everything here is real. "Now" is the live run and its steps; "Files" are the
 * sealed bundles its finished runs left behind; "Activity" is its collaboration
 * feed; "Computer" is the existing workspace terminal. A live picture of its
 * screen is not connected yet, and the tab says so rather than drawing a fake one.
 */
export default function WorkspaceSheet({ agent, threadId, agents, live, steps, approvals = [], onClose }) {
  const [tab, setTab] = useState('now');
  const [files, setFiles] = useState(null);
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    if (tab !== 'files' || files) return;
    api.artifacts().then((r) => setFiles((r.artifacts || []).filter((a) => a.agentId === agent.agentId)))
      .catch(setProblem);
  }, [tab, files, agent.agentId]);

  const state = STATES[live?.state || agent.state] || STATES.idle;
  const trail = useMemo(() => (steps?.items || []).slice(-14).reverse(), [steps]);
  const waiting = approvals.filter((a) => a.status === 'pending');

  return (
    <Sheet title={`${agent.name}'s desk`} tall onClose={onClose}>
      <div className="ws-tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={tab === key ? 'on' : ''} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>

      {tab === 'now' && (
        <div className="ws-pane">
          <div className={`ws-now tone-${state.tone}`}>
            <span className="ws-dot" aria-hidden="true" />
            <div>
              <strong>{state.label}</strong>
              <span>{live?.action || (state.label === 'Idle' ? 'Nothing running right now.' : state.verb)}</span>
            </div>
          </div>

          {waiting.length > 0 && (
            <div className="ws-block">
              <h3>Waiting on you</h3>
              {waiting.map((a) => (
                <div className="ws-item ws-item--ask" key={a.approvalId}>
                  <Icon name="shield" size={17} />
                  <span><strong>{a.action}</strong><small>{a.policy?.reason || 'Needs your decision'}</small></span>
                </div>
              ))}
            </div>
          )}

          <div className="ws-block">
            <h3>Tool activity</h3>
            {trail.length === 0 && <p className="ws-none">No tool activity in this conversation yet.</p>}
            {trail.map((s, i) => (
              <div className="ws-item" key={`${s.at}-${i}`}>
                <Icon name={s.review?.decision === 'denied' ? 'x' : s.review?.decision === 'asked' ? 'shield' : 'check'} size={17} />
                <span>
                  <strong>{s.name}</strong>
                  <small>{s.summary}</small>
                </span>
                {s.review?.decision && <em className={`ws-verdict v-${s.review.decision}`}>{VERDICT[s.review.decision] || s.review.decision}</em>}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'files' && (
        <div className="ws-pane">
          {problem && <Problem error={problem} onRetry={() => { setProblem(null); setFiles(null); }} />}
          {!problem && files === null && <p className="ws-none">Loading…</p>}
          {files?.length === 0 && <p className="ws-none">Nothing sealed yet. When a run finishes, what it did is kept here and never rewritten.</p>}
          {files?.map((f) => (
            <div className="ws-item" key={f.runId}>
              <Icon name="layers" size={17} />
              <span><strong>{f.goal || f.runId}</strong><small>{f.summary || f.outcome} · {ago(f.endedAt || f.startedAt)}</small></span>
            </div>
          ))}
        </div>
      )}

      {tab === 'activity' && (
        <div className="ws-pane"><Activity threadId={threadId} agents={agents} /></div>
      )}

      {tab === 'computer' && (
        <div className="ws-pane">
          <p className="ws-none">A live view of the screen isn&apos;t connected yet. Its terminal is.</p>
          <Computer threadId={threadId} agent={agent} />
        </div>
      )}
    </Sheet>
  );
}
