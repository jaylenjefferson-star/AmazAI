import { useMemo, useState } from 'react';
import { api } from '../api';
import Companion from '../characters/Companion';
import { friendly } from '../lib/errors';
import { announceOrgChange, teamOf } from '../lib/org';
import Icon from './Icon';
import Problem from './Problem';
import Sheet, { SheetRow } from './Sheet';

/** The tick on the line it is on now. Said aloud too: a tick alone is not a word. */
const Current = () => <span className="sx-check"><Icon name="check" size={18} /><span className="sr-only">Current</span></span>;

/**
 * Choose who a Bot reports to: you, or another Bot.
 *
 * A Bot's own team is not offered. Moving a Bot beneath someone who already
 * reports up to it would make a loop, and the server refuses that too, but a list
 * that never offers it is kinder than an error after the tap. This is
 * organisation only: it changes where the Bot sits and what it is told about its
 * place, and nothing about what it may do.
 */
export default function ReportsToSheet({ agent, agents, onClose, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [problem, setProblem] = useState(null);

  const options = useMemo(() => {
    const blocked = teamOf(agent.agentId, agents);
    blocked.add(agent.agentId);
    return agents.filter((a) => !blocked.has(a.agentId));
  }, [agent.agentId, agents]);

  const current = agent.managerId || 'owner';

  async function choose(target) {
    if (target === current) { onClose(); return; }
    setBusy(target);
    setProblem(null);
    try {
      await api.updateAgent(agent.agentId, { reportsTo: target });
      announceOrgChange();
      onChanged?.();
      onClose();
    } catch (err) {
      setProblem({ error: err, message: friendly(err, `Couldn't move ${agent.name}. Nothing changed.`) });
      setBusy(null);
    }
  }

  return (
    <Sheet title={`${agent.name} reports to`} onClose={onClose}>
      <div className="sx-group">
        <SheetRow onClick={() => choose('owner')} disabled={!!busy}
                  trailing={current === 'owner' ? <Current /> : null}>
          <span className="sx-icon"><Icon name="user" size={20} /></span>
          <span className="sx-text"><strong>You</strong><small>Top of the chart. No Bot in between.</small></span>
        </SheetRow>
        {options.map((a) => (
          <SheetRow key={a.agentId} onClick={() => choose(a.agentId)} disabled={!!busy}
                    trailing={current === a.agentId ? <Current /> : null}>
            <Companion archetype={a.archetype} color={a.color} state="idle" size={34} decorative />
            <span className="sx-text"><strong>{a.name}</strong>{(a.title || a.role) && <small>{a.title || a.role}</small>}</span>
          </SheetRow>
        ))}
      </div>
      {problem && <Problem error={problem.error} message={problem.message} />}
      <p className="sx-note">
        This is how your team is organised. It doesn&apos;t change what any Bot can do, and you still approve everything that needs approving.
      </p>
    </Sheet>
  );
}
