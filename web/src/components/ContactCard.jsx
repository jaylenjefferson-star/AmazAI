import { createContext, useContext, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import Companion, { STATES } from '../characters/Companion';
import { presentAgent } from '../hooks/useAgents';
import { friendly } from '../lib/errors';
import { usePresence } from '../presence';
import Icon from './Icon';
import Problem from './Problem';
import Sheet from './Sheet';

const Contacts = createContext({ open: () => {} });

/** Tap an agent anywhere -- a speaker label, an @mention, a member list, a
 *  delegation -- and its contact card opens over what you were reading. */
export const useContacts = () => useContext(Contacts);

export function ContactsProvider({ children }) {
  const [id, setId] = useState(null);
  return (
    <Contacts.Provider value={{ open: setId }}>
      {children}
      {id && <ContactCard agentId={id} onClose={() => setId(null)} />}
    </Contacts.Provider>
  );
}

/** A person-shaped button for an agent: avatar and name, opens the card. */
export function ContactChip({ agent, size = 20 }) {
  const { open } = useContacts();
  if (!agent) return null;
  return (
    <button type="button" className="contact-chip" onClick={(e) => { e.stopPropagation(); open(agent.agentId); }}>
      <Companion archetype={agent.archetype} color={agent.color} state="idle" size={size} decorative />
      <span>{agent.name}</span>
    </button>
  );
}

/**
 * Who this agent is, at a glance: the card you would expect from a contact.
 * Identity, what it is doing right now, what it can use, and the four things
 * you actually want to do with a teammate.
 */
function ContactCard({ agentId, onClose }) {
  const navigate = useNavigate();
  const live = usePresence()[agentId];
  const [agent, setAgent] = useState(null);
  const [error, setError] = useState(null);
  const [task, setTask] = useState(null);      // null = closed, string = drafting
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setError(null);
    api.agent(agentId).then((a) => setAgent(presentAgent(a))).catch(setError);
  }, [agentId, attempt]);

  const go = (to, state) => { onClose(); navigate(to, state ? { state } : undefined); };

  if (error) {
    return (
      <Sheet title="" onClose={onClose}>
        <Problem error={error} message={friendly(error, "Couldn't load this contact.")} onRetry={() => setAttempt((n) => n + 1)} />
      </Sheet>
    );
  }
  if (!agent) {
    return <Sheet title="" onClose={onClose}><div className="cc-load" aria-busy="true"><span className="skel skel-avatar" /><span className="skel skel-line" style={{ width: '40%' }} /></div></Sheet>;
  }

  const state = live?.state || agent.state;
  const info = STATES[state] || STATES.idle;
  const doing = live?.action || (info.label === 'Idle' ? 'Available' : info.label);
  const apps = (agent.grants || []).filter((g) => String(g.connectorId).startsWith('composio:'));

  return (
    <Sheet title="" label={`${agent.name}, contact`} onClose={onClose}>
      <div className="cc">
        <Companion archetype={agent.archetype} color={agent.color} state={state} size={92} name={agent.name} />
        <h2>{agent.name}</h2>
        <p className="cc-sub">{[agent.title, agent.role].filter(Boolean).join(' · ') || 'Teammate'}</p>
        <p className={`cc-doing tone-${info.tone}`}><i aria-hidden="true" />{doing}</p>

        <div className="cc-actions">
          <button type="button" onClick={() => go(`/agents/${agent.agentId}`)}><span><Icon name="send" size={20} /></span>Message</button>
          <button type="button" onClick={() => setTask(task === null ? '' : null)} aria-expanded={task !== null}><span><Icon name="task" size={20} /></span>Assign</button>
          <button type="button" onClick={() => go(`/agents/${agent.agentId}`, { profile: true })}><span><Icon name="user" size={20} /></span>Profile</button>
        </div>

        {task !== null && (
          <form className="cc-task" onSubmit={(e) => { e.preventDefault(); if (task.trim()) go(`/agents/${agent.agentId}`, { task: task.trim() }); }}>
            <textarea data-autofocus rows={3} value={task} onChange={(e) => setTask(e.target.value)}
                      placeholder={`What should ${agent.name} do?`} />
            <button className="primary" disabled={!task.trim()}>Assign to {agent.name}</button>
          </form>
        )}

        {agent.description && <section className="cc-block"><h3>About</h3><p>{agent.description}</p></section>}
        <section className="cc-block">
          <h3>Can use</h3>
          <div className="pf-chips">
            {apps.map((g) => <span key={g.connectorId}>{g.connectorId.replace('composio:', '')}{g.capability === 'read' ? ' · read' : ''}</span>)}
            {(agent.allowedTools || []).map((t) => <span key={t}>{{ shell: 'Terminal', file_operations: 'Files', browser: 'Browser', code_interpreter: 'Code' }[t] || t}</span>)}
            {!apps.length && !(agent.allowedTools || []).length && <span>Nothing yet</span>}
          </div>
        </section>
      </div>
    </Sheet>
  );
}
