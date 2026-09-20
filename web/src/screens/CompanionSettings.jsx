import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import Companion from '../characters/Companion';
import { ARCHETYPES, ARCHETYPE_KEYS } from '../characters/archetypes';
import Icon from '../components/Icon';
import { api } from '../api';
import { presentAgent } from '../hooks/useAgents';

/**
 * One companion, everything about it that a person may change.
 *
 * Deliberately only what `PATCHABLE` in `services/amazai/agents.py` accepts.
 * A field here that the API refuses is worse than an absent one: it looks
 * like a setting until someone relies on it, and the refusal arrives as a
 * save failure with no way to act on it.
 *
 * So there is no "computer" control -- `workspace` is not patchable -- and no
 * duplicate or save-as-template, which have no routes at all. The computer,
 * memory, skills and usage all live in the panel the conversation already
 * opens; repeating them here would be a second place to change the same
 * thing, and the two would disagree.
 */

const DAYS = [
  ['Mon', 0], ['Tue', 1], ['Wed', 2], ['Thu', 3], ['Fri', 4], ['Sat', 5], ['Sun', 6],
];

/**
 * The form's values for an agent.
 *
 * Used for both the initial draft and the comparison that decides whether
 * anything changed. Defaulting in only one of the two is what made a freshly
 * loaded form announce "unsaved changes" before it was touched: an agent with
 * no `workingStyle` became `collaborative` in the draft and stayed `undefined`
 * on the other side of the comparison.
 */
function formValues(agent) {
  return {
    name: agent.name || '',
    role: agent.role || '',
    description: agent.description || '',
    systemPrompt: agent.systemPrompt || '',
    workingStyle: agent.workingStyle || 'collaborative',
    modelTier: agent.model?.tier || 'balanced',
    shape: agent.archetype,
    color: agent.color,
    perRunUsd: agent.budget?.perRunUsd ?? 1,
    perMonthUsd: agent.budget?.perMonthUsd ?? 20,
    timezone: agent.timezone || '',
    hoursOn: Boolean(agent.workingHours),
    start: agent.workingHours?.start || '09:00',
    end: agent.workingHours?.end || '17:00',
    days: agent.workingHours?.days || [0, 1, 2, 3, 4],
  };
}

function Group({ title, note, children }) {
  return (
    <section className="setgroup">
      <h2>{title}</h2>
      {note && <p className="setgroup-note">{note}</p>}
      <div className="setgroup-body">{children}</div>
    </section>
  );
}

export default function CompanionSettings() {
  const { agentId } = useParams();
  const navigate = useNavigate();

  const [agent, setAgent] = useState(null);
  const [options, setOptions] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // The edited copy. `null` until the agent loads, so a half-filled form is
  // never shown over a companion whose values have not arrived.
  const [draft, setDraft] = useState(null);

  const load = useCallback(() => {
    api.agent(agentId)
      .then((a) => {
        const shown = presentAgent(a);
        setAgent(shown);
        setDraft(formValues(shown));
      })
      .catch((e) => setError(e.message));
  }, [agentId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api.agentOptions().then(setOptions).catch((e) => setError(e.message));
  }, []);

  const set = (key, value) => {
    setSaved(false);
    setDraft((d) => ({ ...d, [key]: value }));
  };

  /**
   * Only what actually changed.
   *
   * A PATCH carrying every field would re-send the budget and the grants on a
   * rename, and every one of those is an audited privileged change in
   * `plan_update` -- reading the trail afterwards would show a widening that
   * never happened.
   */
  const changes = useMemo(() => {
    if (!agent || !draft) return {};
    const was = formValues(agent);
    const out = {};
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

    for (const key of ['name', 'role', 'description', 'systemPrompt', 'workingStyle']) {
      if (draft[key] !== was[key]) out[key] = draft[key];
    }
    if (draft.modelTier !== was.modelTier) out.modelTier = draft.modelTier;

    if (draft.shape !== was.shape || draft.color !== was.color) {
      out.avatar = { shape: draft.shape, color: draft.color };
    }

    if (Number(draft.perRunUsd) !== Number(was.perRunUsd)
        || Number(draft.perMonthUsd) !== Number(was.perMonthUsd)) {
      out.budget = {
        ...(agent.budget || {}),
        perRunUsd: Number(draft.perRunUsd),
        perMonthUsd: Number(draft.perMonthUsd),
      };
    }

    // An empty box is "unchanged", not "clear it": the API has no way to
    // unset a timezone, and sending '' would be refused as a bad zone.
    if (draft.timezone && draft.timezone !== was.timezone) out.timezone = draft.timezone;

    const hours = draft.hoursOn
      ? { start: draft.start, end: draft.end, days: [...draft.days].sort((a, b) => a - b) }
      : null;
    if (!same(hours, agent.workingHours ?? null)) out.workingHours = hours;

    return out;
  }, [agent, draft]);

  const dirty = Object.keys(changes).length > 0;

  async function save(e) {
    e?.preventDefault();
    if (!dirty || saving) return;
    setSaving(true);
    setError('');
    try {
      const updated = await api.updateAgent(agentId, changes);
      setAgent(presentAgent(updated));
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function archive() {
    // Deactivation, never deletion: the evidence a companion produced has to
    // keep pointing at something. The API enforces this; the wording here
    // just stops the control promising otherwise.
    if (!window.confirm(
      `Archive ${agent.name}? It stops running and frees its seat. Its past runs `
      + 'and their sealed evidence remain.')) return;
    try {
      await api.archiveAgent(agentId);
      navigate('/');
    } catch (err) {
      setError(err.message);
    }
  }

  if (error && !agent) {
    return <div className="page"><div className="empty">
      <strong>Could not open settings</strong><span>{error}</span>
    </div></div>;
  }
  if (!agent || !draft || !options) {
    return <div className="page"><div className="empty">Loading…</div></div>;
  }

  return (
    <div className="page settings-page">
      <header className="chat-head">
        <Link to={`/agents/${agentId}`} className="chat-icon" aria-label="Back to conversation">
          <Icon name="chevronLeft" size={20} />
        </Link>
        <div className="chat-identity">
          <span className="chat-who"><strong>{agent.name}</strong><small>Settings</small></span>
        </div>
        <span className="chat-icon" aria-hidden="true" />
      </header>

      <form onSubmit={save}>
        <Group title="Identity">
          <div className="avatar-stage">
            <Companion archetype={draft.shape} color={draft.color} state="idle" size={88}
                       name={draft.name || agent.name} />
          </div>

          <div className="picker" role="radiogroup" aria-label="Character">
            {ARCHETYPE_KEYS.map((k) => (
              <button key={k} type="button" role="radio" aria-checked={draft.shape === k}
                      aria-label={ARCHETYPES[k].name} title={ARCHETYPES[k].name}
                      className={`swatch lg ${draft.shape === k ? 'on' : ''}`}
                      onClick={() => set('shape', k)}>
                <Companion archetype={k} color={draft.color} state="idle" size={34} />
              </button>
            ))}
          </div>
          <p className="hint-text" style={{ textAlign: 'center', marginTop: 0 }}>
            {ARCHETYPES[draft.shape].name} — {ARCHETYPES[draft.shape].blurb}
          </p>

          <div className="picker" role="radiogroup" aria-label="Colour">
            {options.colors.map((c) => (
              <button key={c} type="button" role="radio" aria-checked={draft.color === c}
                      aria-label={c} title={c}
                      className={`dot-swatch ${draft.color === c ? 'on' : ''}`}
                      style={{ background: c }} onClick={() => set('color', c)} />
            ))}
          </div>

          <label className="field">
            <span>Name</span>
            <input value={draft.name} maxLength={60}
                   onChange={(e) => set('name', e.target.value)} />
          </label>
          <label className="field">
            <span>Title</span>
            <input value={draft.role} maxLength={200}
                   onChange={(e) => set('role', e.target.value)} />
          </label>
          <label className="field">
            <span>Description <em>optional</em></span>
            <textarea rows={2} value={draft.description} maxLength={2000}
                      onChange={(e) => set('description', e.target.value)} />
          </label>
        </Group>

        <Group title="Intelligence"
               note="Instructions are given to the model. They are not a security control — what this companion may do is decided before it runs.">
          <label className="field">
            <span>Instructions</span>
            <textarea rows={6} value={draft.systemPrompt} maxLength={20000}
                      onChange={(e) => set('systemPrompt', e.target.value)} />
          </label>
          <label className="field">
            <span>Personality</span>
            <select value={draft.workingStyle} onChange={(e) => set('workingStyle', e.target.value)}>
              {options.workingStyles.map((w) => <option key={w} value={w}>{w}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Model</span>
            <select value={draft.modelTier} onChange={(e) => set('modelTier', e.target.value)}>
              {options.modelTiers.map((t) => (
                <option key={t.key} value={t.key}>{t.key} — {t.effort} effort</option>
              ))}
            </select>
            {/* Changing the tier reopens the ladder: the concrete model is
                resolved against what the account actually offers, never named
                here. */}
            <p className="hint-text">A tier, not a model name. The identifier is resolved
              against what this account offers.</p>
          </label>
          <p className="setgroup-link">
            <Link to={`/agents/${agentId}`}>Memory and knowledge</Link> live in the
            conversation panel, beside the runs that used them.
          </p>
        </Group>

        <Group title="Work" note="When this companion may start, and what a run may cost.">
          <div className="field-row">
            <label className="field">
              <span>Per run</span>
              <input type="number" min="0" step="0.5" value={draft.perRunUsd}
                     onChange={(e) => set('perRunUsd', e.target.value)} />
            </label>
            <label className="field">
              <span>Per month</span>
              <input type="number" min="0" step="5" max={options.limits.maxMonthlyUsd}
                     value={draft.perMonthUsd}
                     onChange={(e) => set('perMonthUsd', e.target.value)} />
            </label>
          </div>

          <label className="field">
            <span>Timezone <em>IANA name</em></span>
            <input value={draft.timezone} placeholder="America/Los_Angeles"
                   onChange={(e) => set('timezone', e.target.value)} />
            <p className="hint-text">Used when a routine fires, so a schedule means the
              hour where you are — including across a daylight-saving shift.</p>
          </label>

          <label className="check">
            <input type="checkbox" checked={draft.hoursOn}
                   onChange={(e) => set('hoursOn', e.target.checked)} />
            Only start work during set hours
          </label>

          {draft.hoursOn && (
            <>
              <div className="field-row">
                <label className="field">
                  <span>From</span>
                  <input type="time" value={draft.start}
                         onChange={(e) => set('start', e.target.value)} />
                </label>
                <label className="field">
                  <span>To</span>
                  <input type="time" value={draft.end}
                         onChange={(e) => set('end', e.target.value)} />
                </label>
              </div>
              <div className="checks" role="group" aria-label="Days">
                {DAYS.map(([label, n]) => (
                  <label key={n} className={`pick-chip ${draft.days.includes(n) ? 'on' : ''}`}>
                    <input type="checkbox" checked={draft.days.includes(n)}
                           onChange={() => set('days', draft.days.includes(n)
                             ? draft.days.filter((d) => d !== n)
                             : [...draft.days, n])} />
                    {label}
                  </label>
                ))}
              </div>
              {/* Said plainly, because the opposite is the reasonable guess. */}
              <p className="hint-text">Hours decide when a run may <em>start</em>. Work already
                under way is not stopped at the end of the window.</p>
            </>
          )}
        </Group>

        <Group title="Access"
               note="What this companion may reach. Decided here, enforced before the model is invoked — a tool it may not use is absent from what it is offered, not refused afterwards.">
          <p className="setgroup-link">
            <Link to="/connectors">Connectors and grants</Link> are managed for the whole
            workspace, then granted per companion.
          </p>
        </Group>

        <Group title="Management">
          <button type="button" className="danger-btn" onClick={archive}>
            Archive {agent.name}
          </button>
          <p className="hint-text">Archiving stops it running and frees its seat. Its past runs
            and their sealed evidence remain, which is why there is no delete.</p>
        </Group>

        {error && <div className="err"><span className="msg-text">{error}</span></div>}

        {/* A save bar that only exists when there is something to save: a
            permanently enabled Save invites a click that does nothing. */}
        {(dirty || saved) && (
          <div className="savebar" role="status">
            <span>{dirty ? 'Unsaved changes' : 'Saved'}</span>
            {dirty && (
              <button className="primary" disabled={saving}>
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            )}
          </div>
        )}
      </form>
    </div>
  );
}
