import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import Connectors from './Connectors';

/**
 * Plugins and skills, in one place.
 *
 * Two different things, and the page keeps them apart. A **plugin** connects the
 * organisation to a tool: nothing can use it until a Bot is granted it on its own
 * profile. A **skill** is a way of doing something -- a playbook -- and nothing
 * sees it until it is assigned to a Bot. Both are opt-in per Bot; installing here
 * grants no one anything.
 */

function Skills() {
  const [skills, setSkills] = useState(null);
  const [agents, setAgents] = useState([]);
  const [target, setTarget] = useState({});          // skillId -> agentId chosen
  const [note, setNote] = useState({});              // skillId -> feedback
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: '', description: '', body: '' });
  const [busy, setBusy] = useState('');

  async function load() {
    try {
      const [s, a] = await Promise.all([api.skills(), api.agents()]);
      setSkills(s.skills || []);
      setAgents((a.agents || []).filter((x) => x.status !== 'archived'));
    } catch (err) { setError(err.message); }
  }
  useEffect(() => { load(); }, []);

  async function approve(skill) {
    setBusy(skill.skillId); setError('');
    try { await api.updateSkill(skill.skillId, { status: 'active' }); await load(); }
    catch (err) { setError(err.message); } finally { setBusy(''); }
  }

  async function assign(skill) {
    const agentId = target[skill.skillId];
    if (!agentId) return;
    setBusy(skill.skillId); setError('');
    try {
      await api.assignSkill(skill.skillId, agentId, skill.currentVersion);
      const who = agents.find((a) => a.agentId === agentId)?.name || agentId;
      setNote((n) => ({ ...n, [skill.skillId]: `Given to ${who}` }));
    } catch (err) { setError(err.message); } finally { setBusy(''); }
  }

  async function create(e) {
    e.preventDefault();
    setBusy('new'); setError('');
    try {
      await api.createSkill({ name: draft.name.trim(), description: draft.description.trim(), body: draft.body.trim() });
      setDraft({ name: '', description: '', body: '' });
      setCreating(false);
      await load();
    } catch (err) { setError(err.message); } finally { setBusy(''); }
  }

  const proposed = (skills || []).filter((s) => s.status === 'proposed');
  const library = (skills || []).filter((s) => s.status === 'active');

  return (
    <section aria-label="Skills">
      {error && <div className="err" style={{ marginBottom: 12 }}><span className="msg-text">{error}</span></div>}

      {proposed.length > 0 && (
        <>
          <h3 className="mk-h">Waiting for you</h3>
          {proposed.map((s) => (
            <div key={s.skillId} className="row-card mk-skill">
              <div className="mk-text">
                <strong>{s.name}</strong>
                <small>{s.description}</small>
                <em>Proposed by {s.proposedBy || 'a Bot'}; no Bot can use it until you approve it.</em>
              </div>
              <button className="primary sm" disabled={busy === s.skillId} onClick={() => approve(s)}>Approve</button>
            </div>
          ))}
        </>
      )}

      <div className="mk-bar">
        <h3 className="mk-h">Library</h3>
        <button className="btn-link" onClick={() => setCreating((c) => !c)}>{creating ? 'Cancel' : 'New skill'}</button>
      </div>

      {creating && (
        <form className="row-card mk-form" onSubmit={create}>
          <label className="field"><span>Name</span>
            <input id="mk-name" value={draft.name} maxLength={80} autoFocus
                   onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label className="field"><span>What is it for?</span>
            <input id="mk-desc" value={draft.description} maxLength={300}
                   onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
          <label className="field"><span>The playbook</span>
            <textarea id="mk-body" rows={6} value={draft.body}
                      onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></label>
          <button className="primary" disabled={busy === 'new' || draft.name.trim().length < 2 || !draft.description.trim()}>
            {busy === 'new' ? 'Saving…' : 'Save skill'}
          </button>
        </form>
      )}

      {skills === null && <div className="empty">Loading…</div>}
      {skills && library.length === 0 && !creating && (
        <div className="empty">
          <span className="title">No skills yet</span>
          <span>Save a good answer as a skill from any conversation, or write one here.</span>
        </div>
      )}
      {library.map((s) => (
        <div key={s.skillId} className="row-card mk-skill">
          <div className="mk-text">
            <strong>{s.name} <em className="mem-kind">v{s.currentVersion}</em></strong>
            <small>{s.description}</small>
            {note[s.skillId] && <em role="status">{note[s.skillId]}</em>}
          </div>
          <div className="mk-assign">
            <select aria-label={`Give ${s.name} to`} value={target[s.skillId] || ''}
                    onChange={(e) => setTarget({ ...target, [s.skillId]: e.target.value })}>
              <option value="">Give to…</option>
              {agents.map((a) => <option key={a.agentId} value={a.agentId}>{a.name}</option>)}
            </select>
            <button className="sm" disabled={!target[s.skillId] || busy === s.skillId} onClick={() => assign(s)}>Give</button>
          </div>
        </div>
      ))}
    </section>
  );
}

export default function Marketplace() {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'skills' ? 'skills' : 'plugins';

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Marketplace</h1>
          <p>Plugins connect your Bots to your tools. Skills teach them how you work.
            Neither gives a Bot anything until you grant or assign it.</p>
        </div>
      </header>
      <div className="seg" role="tablist" aria-label="Marketplace">
        {[['plugins', 'Plugins'], ['skills', 'Skills']].map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={tab === key ? 'on' : ''}
                  onClick={() => setParams(key === 'plugins' ? {} : { tab: key }, { replace: true })}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'plugins' ? <Connectors embedded /> : <Skills />}
    </div>
  );
}
