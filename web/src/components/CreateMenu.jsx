import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Companion from '../characters/Companion';
import { api } from '../api';
import { COPY, friendly } from '../lib/errors';
import Problem from './Problem';
import Sheet, { SheetRow } from './Sheet';
import ToolsSheet from './ToolsSheet';

// Mirrors `collab.MAX_ROOM_MEMBERS`; the API is the authority.
const MAX_ROOM = 4;

function Picker({ agents, picked, setPicked, max = MAX_ROOM, single = false }) {
  return (
    <div className="cm-picks" role="group">
      {agents.map((a) => {
        const on = picked.includes(a.agentId);
        return (
          <button type="button" key={a.agentId} aria-pressed={on}
                  className={`cm-pick${on ? ' on' : ''}`}
                  disabled={!on && !single && picked.length >= max}
                  onClick={() => setPicked((cur) => (single ? [a.agentId]
                    : on ? cur.filter((x) => x !== a.agentId) : [...cur, a.agentId]))}>
            <Companion archetype={a.archetype} color={a.color} state="idle" size={22} />
            <span>{a.name}</span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * What the + at the top of the roster opens.
 *
 * Five things you can start, in the order people reach for them. Each is either a
 * full screen of its own (an agent deserves one) or a small form in this same
 * sheet -- nothing here is a dead end.
 */
export default function CreateMenu({ agents, onClose, onCreated }) {
  const navigate = useNavigate();
  const [view, setView] = useState('menu');     // menu | room | task | tools
  const [title, setTitle] = useState('');
  const [picked, setPicked] = useState([]);
  const [goal, setGoal] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const none = !agents.length;

  const go = (to, state) => { onClose(); navigate(to, state ? { state } : undefined); };

  async function makeRoom(e) {
    e.preventDefault();
    if (busy || !title.trim() || !picked.length) return;
    setBusy(true);
    setProblem(null);
    try {
      const room = await api.createThread({ kind: 'room', title: title.trim(), agentIds: picked });
      onCreated(`/rooms/${room.threadId}`);
    } catch (err) {
      setProblem({ error: err, message: friendly(err, COPY.createRoom) });
      setBusy(false);
    }
  }

  function assign(e) {
    e.preventDefault();
    if (!goal.trim() || !picked.length) return;
    // The task is handed to the conversation, which sends it as the first message.
    go(`/agents/${picked[0]}`, { task: goal.trim() });
  }

  if (view === 'tools') return <ToolsSheet onClose={onClose} back={() => setView('menu')} />;

  if (view === 'room') {
    return (
      <Sheet title="New room" onClose={onClose} back={() => setView('menu')}>
        <form className="cm-form" onSubmit={makeRoom}>
          <label>
            <span>What is it for?</span>
            <input data-autofocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ship the console" />
          </label>
          <div>
            <span className="cm-label">Who is in it? Up to four.</span>
            <Picker agents={agents} picked={picked} setPicked={setPicked} />
          </div>
          {problem && <Problem message={problem.message} error={problem.error} inline />}
          <button className="primary cm-go" disabled={busy || !title.trim() || !picked.length}>
            {busy ? 'Creating…' : 'Create room'}
          </button>
        </form>
      </Sheet>
    );
  }

  if (view === 'task') {
    return (
      <Sheet title="New task" onClose={onClose} back={() => setView('menu')}>
        <form className="cm-form" onSubmit={assign}>
          <div>
            <span className="cm-label">Who should take it?</span>
            <Picker agents={agents} picked={picked} setPicked={setPicked} single />
          </div>
          <label>
            <span>What needs doing?</span>
            <textarea data-autofocus rows={4} value={goal} onChange={(e) => setGoal(e.target.value)}
                      placeholder="Draft a reply to the Brightside thread and flag anything I need to decide." />
          </label>
          <button className="primary cm-go" disabled={!goal.trim() || !picked.length}>Assign</button>
        </form>
      </Sheet>
    );
  }

  return (
    <Sheet title="Create" onClose={onClose}>
      <div className="sx-group">
        <SheetRow icon="bot" title="New Agent" hint="Hire an AI teammate" onClick={() => go('/agents/new')} />
        <SheetRow icon="hash" title="New Room" hint={none ? 'Create an agent first' : 'Several agents and you, one thread'}
                  disabled={none} onClick={() => setView('room')} />
        <SheetRow icon="plug" title="Connect Tool" hint="Give your agents an app to use" onClick={() => setView('tools')} />
        <SheetRow icon="clock" title="New Routine" hint={none ? 'Create an agent first' : 'Work that runs on its own'}
                  disabled={none} onClick={() => go('/routines/new')} />
        <SheetRow icon="task" title="New Task" hint={none ? 'Create an agent first' : 'Hand something to an agent'}
                  disabled={none} onClick={() => setView('task')} />
      </div>
    </Sheet>
  );
}
