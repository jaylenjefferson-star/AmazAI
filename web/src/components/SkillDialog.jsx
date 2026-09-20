import { useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Save part of a conversation as a skill.
 *
 * A skill is a reusable way of doing something -- a playbook every assigned Bot
 * can follow. This turns one message into one, pre-filled, so the useful part of
 * a good answer does not have to be re-explained next time.
 *
 * It writes through the ordinary `POST /skills`; a person authoring a skill goes
 * straight to active, exactly as on the Marketplace screen. Assigning it to this
 * Bot is offered, and on by default, but it is a separate step underneath: a
 * skill nobody is assigned to is in no Bot's context.
 */
export default function SkillDialog({ draft, threadId, agentId, onClose, onSaved }) {
  const firstLine = String(draft.text || '').split('\n').find((l) => l.trim()) || '';
  // A name is a handle, not a sentence: the first few words, editable.
  const [name, setName] = useState(
    firstLine.replace(/[^\w \-'&,.()/]/g, '').split(/\s+/).filter(Boolean).slice(0, 5).join(' ').slice(0, 60));
  const [description, setDescription] = useState('');
  const [body, setBody] = useState(draft.text || '');
  const [assign, setAssign] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const ok = name.trim().length >= 2 && description.trim().length >= 1 && !busy;

  async function save(e) {
    e.preventDefault();
    if (!ok) return;
    setBusy(true);
    setError('');
    try {
      const skill = await api.createSkill({
        name: name.trim(), description: description.trim(), body: body.trim(),
        sourceThreadId: threadId,
      });
      if (assign && agentId) await api.assignSkill(skill.skillId, agentId, skill.currentVersion);
      onSaved(skill);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="skill-dialog-title">
        <header className="modal-head">
          <button className="ghost sm" onClick={onClose} aria-label="Close">✕</button>
          <h2 id="skill-dialog-title">Save as a skill</h2>
        </header>
        <form onSubmit={save}>
          <div className="modal-body">
            <label className="field">
              <span>Name</span>
              <input id="skill-name" value={name} maxLength={80} autoFocus
                     onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              <span>What is it for?</span>
              <input id="skill-description" value={description} maxLength={300}
                     placeholder="One line a Bot can read to know when to use it"
                     onChange={(e) => setDescription(e.target.value)} />
            </label>
            <label className="field">
              <span>The playbook</span>
              <textarea id="skill-body" rows={8} value={body}
                        onChange={(e) => setBody(e.target.value)} />
            </label>
            <label className="check">
              <input id="skill-assign" type="checkbox" checked={assign}
                     onChange={(e) => setAssign(e.target.checked)} />
              <span>Give it to this Bot now</span>
            </label>
            {error && <div className="err"><span className="msg-text">{error}</span></div>}
          </div>
          <footer className="modal-foot">
            <button type="button" className="ghost" onClick={onClose}>Cancel</button>
            <button className="primary" disabled={!ok}>{busy ? 'Saving…' : 'Save skill'}</button>
          </footer>
        </form>
      </div>
    </div>
  );
}
