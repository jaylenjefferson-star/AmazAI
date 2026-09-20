import { useEffect, useMemo, useState } from 'react';
import Companion from '../characters/Companion';
import { ruleSentence } from './StepsGroup';

function remaining(expiresAt) {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return null;
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return { text: `${m}:${String(s).padStart(2, '0')}`, ms };
}

/**
 * The three things a Bot can *propose* rather than do. Each is the same approval
 * underneath -- argument-bound, expiring to denied -- but a person is being
 * asked "make this Bot?", not "allow this call?", and the buttons should say so.
 * `fields` picks what to show: the proposal itself, not the plumbing around it.
 */
const PROPOSALS = {
  'agent.create': {
    title: 'A Bot is proposed', yes: 'Create Bot', no: 'Not now',
    fields: ['name', 'role', 'description'],
    note: 'It starts with no connectors and a small budget; you widen either later.',
  },
  'skill.create': {
    title: 'A skill is proposed', yes: 'Save skill', no: 'Not now',
    fields: ['name', 'description', 'body'],
    note: 'No Bot can use it until you assign it.',
  },
  'memory.publish': {
    title: 'Something to share with every Bot', yes: 'Share with all', no: 'Not now',
    fields: ['title', 'body', 'kind'],
    note: 'Every Bot will see this in its context. You can revoke it at any time.',
  },
};

const ACTION_LABELS = {
  'agent.create': 'Create a new Bot',
  'skill.create': 'Save a new skill',
  'memory.publish': 'Publish to shared memory',
};

function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

const clip = (v, n = 220) => {
  const text = typeof v === 'object' ? JSON.stringify(v) : String(v);
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
};

/**
 * Every field here comes from the tool call's arguments, never from
 * model-authored prose. An injected model must not be able to write its own
 * approval card.
 *
 * The rule that stopped the run is named -- "on the always-approve floor
 * (slack.post)" -- because the question in a person's head is not "what is this"
 * but "why is this asking when the last one didn't". It comes from
 * `policy.Decision`, stored on the approval when it was requested.
 *
 * The expiry is drawn as well as counted. An approval that runs out expires
 * to DENIED — so the bar draining to empty is a safe outcome, and is allowed
 * to look like one right up until it is nearly gone.
 */
export default function ApprovalCard({ approval, onDecide }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(() => remaining(approval.expiresAt));

  useEffect(() => {
    const t = setInterval(() => setLeft(remaining(approval.expiresAt)), 1000);
    return () => clearInterval(t);
  }, [approval.expiresAt]);

  // Total window, measured once, so the bar has a stable denominator even
  // though the card may mount well after the request was made.
  const total = useMemo(() => {
    const issued = approval.requestedAt ? new Date(approval.requestedAt).getTime() : null;
    const expires = new Date(approval.expiresAt).getTime();
    return issued ? Math.max(1, expires - issued) : Math.max(1, left?.ms ?? 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval.approvalId]);

  const proposal = PROPOSALS[approval.action];
  const settled = approval.status !== 'pending';
  const expired = !left && !settled;
  const urgent = !!left && left.ms < 60000;
  const high = approval.risk === 'high' && !proposal;
  const irreversible = approval.reversible === false && !proposal;

  async function decide(approve) {
    setBusy(true);
    try { await onDecide(approve, note.trim() || undefined); }
    finally { setBusy(false); }
  }

  const target = approval.target || {};
  const args = approval.arguments || {};
  const shown = proposal
    ? proposal.fields.filter((k) => args[k]).map((k) => [k, args[k]])
    : Object.entries(args);
  const pct = left ? Math.max(0, Math.min(100, (left.ms / total) * 100)) : 0;
  const rule = approval.policy?.rule ? ruleSentence(approval.policy) : '';

  return (
    <div className={`approval enter ${high ? 'high' : ''} ${proposal ? 'proposal' : ''} ${settled || expired ? 'settled' : ''}`}>
      <div className="head">
        <h4>
          {settled ? `${proposal ? proposal.title.replace(/^A |^Something to /, '') : 'Approval'} ${approval.status}`
            : expired ? 'Expired — denied'
            : proposal ? proposal.title : 'Approval required'}
        </h4>
        {!settled && !expired && !proposal && (
          <span className="risk-tag">{high ? 'high risk' : 'review'}</span>
        )}
      </div>

      {!settled && !expired && (
        <div className="drain" role="presentation">
          <i className={urgent ? 'urgent' : ''} style={{ width: `${pct}%` }} />
        </div>
      )}

      {irreversible && !settled && !expired && (
        <div className="irreversible">
          <span aria-hidden="true">⚠</span>
          <span>This action cannot be undone.</span>
        </div>
      )}

      {approval.action === 'agent.create' && args.name && (
        <div className="proposal-mark">
          <Companion archetype={args.avatar?.shape || 'pebble'} color={args.avatar?.color || '#12a594'}
                     state="idle" size={40} name={args.name} />
        </div>
      )}

      <dl>
        <dt>Action</dt><dd>{actionLabel(approval.action)}</dd>
        {shown.map(([k, v]) => (
          <span key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd className={k === 'body' ? 'prose' : undefined}>{clip(v)}</dd>
          </span>
        ))}
        {!proposal && target.account && (
          <><dt>Account</dt><dd>{target.account}{target.env ? ` (${target.env})` : ''}</dd></>
        )}
        {!proposal && target.region && <><dt>Region</dt><dd>{target.region}</dd></>}
        {!proposal && target.repo && <><dt>Repo</dt><dd>{target.repo}</dd></>}
        {!proposal && approval.reversible != null && (
          <><dt>Reversible</dt><dd>{approval.reversible ? 'yes' : 'no'}</dd></>
        )}
        {approval.why && <><dt>Why</dt><dd className="prose">{approval.why}</dd></>}
        {approval.requestedBy?.agentId && (
          <><dt>Requested by</dt><dd>
            {approval.requestedBy.agentId}
            {approval.requestedBy.routineId ? ` · routine ${approval.requestedBy.routineId}` : ''}
          </dd></>
        )}
      </dl>

      {rule && (
        <div className="rule" data-rule={approval.policy.rule}>
          <span className="rule-label">Why you are being asked</span>
          <span className="rule-text">{rule}</span>
        </div>
      )}
      {proposal && !settled && !expired && <div className="proposal-note">{proposal.note}</div>}

      {!settled && !expired && (
        <>
          <input placeholder="Optional note, recorded either way…" value={note}
                 onChange={(e) => setNote(e.target.value)} style={{ marginBottom: 11 }} />
          <div className="actions">
            <button className={high ? 'danger' : 'primary'} disabled={busy}
                    onClick={() => decide(true)}>
              {busy ? '…' : proposal ? proposal.yes : high ? 'Approve anyway' : 'Approve'}
            </button>
            <button className="ghost" disabled={busy} onClick={() => decide(false)}>
              {proposal ? proposal.no : 'Deny'}
            </button>
            <span className={`expiry ${urgent ? 'urgent' : ''}`}>expires in {left?.text}</span>
          </div>
        </>
      )}

      {settled && approval.note && (
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>Note: {approval.note}</div>
      )}
    </div>
  );
}
