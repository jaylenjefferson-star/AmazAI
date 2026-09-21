import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import { useContacts } from '../components/ContactCard';
import Icon from '../components/Icon';
import Problem from '../components/Problem';
import { RosterSkeleton } from '../components/Skeleton';
import UserAvatar from '../components/UserAvatar';
import { useAgents } from '../hooks/useAgents';
import { friendly } from '../lib/errors';
import { buildTree, countBelow, ORG_CHANGED } from '../lib/org';
import { usePresence } from '../presence';

/** One Bot: who it is, what it is doing right now, and how many report to it. */
function Node({ node, depth }) {
  const { open } = useContacts();
  const live = usePresence()[node.agent.agentId];
  const [folded, setFolded] = useState(false);
  const a = node.agent;
  const state = live?.state || a.state;
  const info = STATES[state] || STATES.idle;
  const below = countBelow(node);
  const direct = node.children.length;

  return (
    <li className="oc-item" style={{ '--i': depth }}>
      <div className={`oc-node${a.entrypoint ? ' is-chief' : ''}`}>
        <button type="button" className="oc-card" onClick={() => open(a.agentId)}
                aria-label={`${a.name}${a.title || a.role ? `, ${a.title || a.role}` : ''}. ${info.label}. Open contact card`}>
          <span className="oc-avatar">
            <Companion archetype={a.archetype} color={a.color} state={state} size={40} decorative />
            <i className={`oc-dot tone-${info.tone}`} aria-hidden="true" />
          </span>
          <span className="oc-text">
            <strong>{a.name}</strong>
            <small>{a.title || a.role || 'Teammate'}</small>
          </span>
        </button>
        {direct > 0 && (
          <button type="button" className={`oc-fold${folded ? ' is-folded' : ''}`} aria-expanded={!folded}
                  aria-label={`${folded ? 'Show' : 'Hide'} the ${below} ${below === 1 ? 'Bot' : 'Bots'} under ${a.name}`}
                  onClick={() => setFolded((f) => !f)}>
            <Icon name="forward" size={14} />
            <span>{below}</span>
          </button>
        )}
      </div>
      {direct > 0 && !folded && (
        <ul className="oc-branch">
          {node.children.map((c) => <Node key={c.agent.agentId} node={c} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
}

/**
 * Who reports to whom.
 *
 * You at the top, Chief beneath you, and every other Bot under Chief unless it was
 * started or moved somewhere else. On a desktop it is a chart; on a phone the same
 * tree is an indented list, because a wide chart is the wrong thing to pinch around
 * on a small screen. Tap a Bot for its card, and change its line from there.
 *
 * It is a picture of how the team is organised, not of what anyone may do: a
 * reporting line grants nothing, and the chart says so.
 */
export default function OrgChart() {
  const { agents, loading, error, reload } = useAgents();
  const roots = useMemo(() => buildTree(agents), [agents]);
  const chief = agents.find((a) => a.entrypoint);

  // A line changed from a contact card: redraw.
  useEffect(() => {
    window.addEventListener(ORG_CHANGED, reload);
    return () => window.removeEventListener(ORG_CHANGED, reload);
  }, [reload]);

  return (
    <div className="page oc">
      <header className="page-head">
        <div>
          <h1>Org chart</h1>
          <p>{chief ? `Every Bot reports to ${chief.name} unless you set it otherwise.` : 'Everyone here reports to you.'}</p>
        </div>
      </header>

      {error && !agents.length && <Problem error={error} message={friendly(error, "Couldn't load your team.")} onRetry={reload} />}
      {loading && !agents.length && !error && <RosterSkeleton rows={4} />}

      {!loading && !error && !agents.length && (
        <div className="empty">
          <strong>No Bots yet</strong>
          <p>Meet your first Bot and the chart starts here.</p>
          <Link className="btn-link primary" to="/agents/new">New Bot</Link>
        </div>
      )}

      {agents.length > 0 && (
        <>
          <div className="oc-scroll">
            <ul className="oc-tree" aria-label="Org chart">
              <li className="oc-item oc-root">
                <div className="oc-node">
                  <div className="oc-card oc-card--you">
                    <span className="oc-avatar"><UserAvatar size={40} /></span>
                    <span className="oc-text"><strong>You</strong><small>Owner</small></span>
                  </div>
                </div>
                <ul className="oc-branch">
                  {roots.map((n) => <Node key={n.agent.agentId} node={n} depth={1} />)}
                </ul>
              </li>
            </ul>
          </div>
          <p className="oc-foot">
            Tap a Bot to open its card and change who it reports to. This is how the team is organised. It doesn&apos;t change what any Bot can do, and you still approve everything that needs approving.
          </p>
        </>
      )}
    </div>
  );
}
