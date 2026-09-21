/**
 * The org chart, from the roster.
 *
 * The server decides who reports to whom (`services/amazai/org.py`: Chief unless
 * a person chose otherwise) and puts the answer on every agent as `managerId`
 * (an agent's id, or null for "reports to you"). Nothing here re-derives that;
 * this only shapes it for drawing. A reporting line is organisation, not
 * authority: it changes nothing about what a Bot may do.
 */

/** Fired after a reporting line changes, so an open chart redraws. */
export const ORG_CHANGED = 'amazai:org-changed';

export const announceOrgChange = () => {
  try { window.dispatchEvent(new Event(ORG_CHANGED)); } catch { /* not in a browser */ }
};

// Chief first, then by name: the same order everywhere the team is listed.
const byRank = (a, b) => (Number(!!b.entrypoint) - Number(!!a.entrypoint))
  || String(a.name).localeCompare(String(b.name));

/**
 * `[{ agent, children: [...] }]`: the Bots that report straight to you, each with
 * the Bots under them. Every Bot appears exactly once, even if the data is odd:
 * one whose manager is missing, or that sits in a loop the server should have cut,
 * is placed at the top rather than dropped from the chart.
 */
export function buildTree(agents) {
  const ids = new Set(agents.map((a) => a.agentId));
  const under = new Map();
  const top = [];
  for (const a of agents) {
    const manager = a.managerId && a.managerId !== a.agentId && ids.has(a.managerId) ? a.managerId : null;
    if (manager) under.set(manager, [...(under.get(manager) || []), a]);
    else top.push(a);
  }

  const placed = new Set();
  const grow = (agent) => {
    placed.add(agent.agentId);
    const kids = (under.get(agent.agentId) || []).filter((c) => !placed.has(c.agentId)).sort(byRank);
    return { agent, children: kids.map(grow) };
  };

  const roots = top.sort(byRank).map(grow);
  for (const a of [...agents].sort(byRank)) if (!placed.has(a.agentId)) roots.push(grow(a));
  return roots;
}

/** Everyone under an agent, at any depth: who it cannot be moved beneath. */
export function teamOf(agentId, agents) {
  const found = new Set();
  const queue = [agentId];
  while (queue.length) {
    const head = queue.shift();
    for (const a of agents) {
      if (a.managerId === head && !found.has(a.agentId) && a.agentId !== agentId) {
        found.add(a.agentId);
        queue.push(a.agentId);
      }
    }
  }
  return found;
}

/** How many Bots sit below a node, at any depth. */
export const countBelow = (node) => node.children.reduce((n, c) => n + 1 + countBelow(c), 0);
