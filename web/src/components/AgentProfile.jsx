import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import Companion from '../characters/Companion';
import { usePins } from '../hooks/usePins';
import { friendly } from '../lib/errors';
import { Activity, Memory, Skills, Usage } from './RightPanel';
import Icon from './Icon';
import Problem from './Problem';
import Sheet from './Sheet';
import ToolsSheet from './ToolsSheet';

const BUILT_IN = {
  shell: 'Terminal', file_operations: 'Files', browser: 'Browser', code_interpreter: 'Code',
};

const TIER_COPY = {
  fast: ['Fast', 'Quick answers, light work.'],
  balanced: ['Balanced', 'The right pick for most jobs.'],
  deep: ['Deep', 'Slower, for hard problems.'],
};

const isOurs = (id) => String(id || '').startsWith('composio:');

/** One collapsed row of the profile. Progressive disclosure: the row says what is
 *  inside in a few words, and the rest appears only when you ask for it. */
function Section({ icon, title, summary, open, onToggle, children }) {
  return (
    <section className={`pf-sec${open ? ' is-open' : ''}`}>
      <button type="button" className="pf-sec-head" aria-expanded={open} onClick={onToggle}>
        <span className="sx-icon"><Icon name={icon} size={19} /></span>
        <span className="sx-text"><strong>{title}</strong>{summary && <small>{summary}</small>}</span>
        <Icon name={open ? 'x' : 'forward'} size={16} />
      </button>
      {open && <div className="pf-sec-body">{children}</div>}
    </section>
  );
}

function Field({ label, value, onChange, multiline = false, placeholder = '', maxLength }) {
  return (
    <label className="pf-field">
      <span>{label}</span>
      {multiline
        ? <textarea rows={4} value={value} maxLength={maxLength} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        : <input value={value} maxLength={maxLength} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />}
    </label>
  );
}

/**
 * One agent, as one persistent identity.
 *
 * Name, title, role and description are not display metadata: the runtime tells the
 * agent who it is from exactly these fields (agentcore.identity_block), so editing
 * one here changes how the agent understands itself on its next turn. Tools,
 * permissions and autonomy sit beside identity because together they are what the
 * agent *is allowed to be*, and every one of them is a real setting the server
 * enforces -- none of it is decoration.
 */
export default function AgentProfile({ agent, agents, threadId, onChange, onClose, onExport }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState('identity');
  const [form, setForm] = useState({
    name: agent.name || '', title: agent.title || '', role: agent.role || '',
    description: agent.description || '', systemPrompt: agent.systemPrompt || '',
    workingStyle: agent.workingStyle || '',
  });
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState(null);
  const [saved, setSaved] = useState('');
  const [options, setOptions] = useState(null);
  const [installed, setInstalled] = useState({});
  const [adding, setAdding] = useState(false);
  const { pins, toggle: togglePin } = usePins();
  const pinned = pins.includes(`dm-${agent.agentId}`);

  useEffect(() => {
    api.agentOptions().then(setOptions).catch(() => {});
    api.connectors().then((r) => setInstalled(Object.fromEntries(
      (r.connectors || []).map((c) => [c.connectorId, c])))).catch(() => {});
  }, [adding]);

  const toggle = (key) => setOpen((cur) => (cur === key ? '' : key));
  const set = (key) => (value) => { setForm((f) => ({ ...f, [key]: value })); setSaved(''); };
  const dirty = ['name', 'title', 'role', 'description', 'systemPrompt', 'workingStyle']
    .some((k) => (form[k] || '') !== (agent[k] || ''));

  const run = useCallback(async (fn, okMessage) => {
    setProblem(null);
    try {
      await fn();
      if (okMessage) setSaved(okMessage);
      onChange?.();
    } catch (err) {
      setProblem({ error: err, message: friendly(err, "Couldn't save that. Nothing was changed.") });
    }
  }, [onChange]);

  async function save() {
    setSaving(true);
    const changes = {};
    for (const k of ['name', 'title', 'role', 'description', 'systemPrompt', 'workingStyle']) {
      if ((form[k] || '') !== (agent[k] || '') && (form[k] || form[k] === '')) changes[k] = form[k];
    }
    await run(() => api.updateAgent(agent.agentId, changes), 'Saved. It will know on its next message.');
    setSaving(false);
  }

  if (adding) return <ToolsSheet onClose={() => { setAdding(false); onChange?.(); }} back={() => { setAdding(false); onChange?.(); }} />;

  const grants = (agent.grants || []).filter((g) => isOurs(g.connectorId));
  const pre = agent.preapproved || [];
  const tier = agent.model?.tier || agent.modelTier || 'balanced';
  const appName = (id) => installed[id]?.name || id.replace(/^composio:/, '');

  return (
    <Sheet title="" label={`${agent.name}, profile`} tall onClose={onClose}>
      <div className="pf-hero">
        <Companion archetype={agent.archetype} color={agent.color} state={agent.state} size={84} name={agent.name} />
        <h2>{agent.name}</h2>
        <p>{[agent.title, agent.role].filter(Boolean).join(' · ') || 'A new teammate'}</p>
        <div className="pf-hero-actions">
          <button type="button" className={pinned ? 'on' : ''}
                  onClick={() => togglePin(`dm-${agent.agentId}`).catch((e) => setProblem({ error: e, message: friendly(e) }))}>
            <Icon name="pin" size={16} />{pinned ? 'Pinned' : 'Pin'}
          </button>
          {onExport && <button type="button" onClick={onExport}><Icon name="download" size={16} />Export</button>}
        </div>
      </div>

      {problem && <Problem message={problem.message} error={problem.error} inline />}
      {saved && <div className="pf-saved" role="status">{saved}</div>}

      <div className="pf-stack">
        <Section icon="user" title="Identity" summary={agent.description || 'Name, title, role'} open={open === 'identity'} onToggle={() => toggle('identity')}>
          <Field label="Name" value={form.name} onChange={set('name')} maxLength={40} />
          <Field label="Title" value={form.title} onChange={set('title')} maxLength={24} placeholder="Operations" />
          <Field label="Role" value={form.role} onChange={set('role')} maxLength={200} placeholder="Sr Director, Head of Ops" />
          <Field label="Description" value={form.description} onChange={set('description')} multiline maxLength={600}
                 placeholder="Head of business operations and strategy" />
          <p className="pf-hint">This is how {agent.name} understands itself. Change it here and its next reply follows.</p>
          <button type="button" className="primary pf-save" disabled={!dirty || saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
        </Section>

        <Section icon="spark" title="Brain" summary={`${(TIER_COPY[tier] || [tier])[0]} model`} open={open === 'brain'} onToggle={() => toggle('brain')}>
          <div className="pf-tiers" role="radiogroup" aria-label="Model">
            {(options?.modelTiers || Object.keys(TIER_COPY).map((key) => ({ key }))).map((t) => (
              <button key={t.key} type="button" role="radio" aria-checked={tier === t.key}
                      className={tier === t.key ? 'on' : ''}
                      onClick={() => tier !== t.key && run(() => api.updateAgent(agent.agentId, { modelTier: t.key }), 'Model changed.')}>
                <strong>{(TIER_COPY[t.key] || [t.key])[0]}</strong>
                <small>{(TIER_COPY[t.key] || [])[1]}</small>
              </button>
            ))}
          </div>
          {options?.workingStyles && (
            <label className="pf-field">
              <span>Personality</span>
              <select value={form.workingStyle} onChange={(e) => set('workingStyle')(e.target.value)}>
                <option value="">Default</option>
                {options.workingStyles.map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
            </label>
          )}
          <Field label="Core instructions" value={form.systemPrompt} onChange={set('systemPrompt')} multiline maxLength={6000}
                 placeholder="How it should think and work. Optional." />
          <button type="button" className="primary pf-save" disabled={!dirty || saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
        </Section>

        <Section icon="plug" title="Tools"
                 summary={grants.length ? `${grants.length} app${grants.length === 1 ? '' : 's'} connected` : 'No apps yet'}
                 open={open === 'tools'} onToggle={() => toggle('tools')}>
          {grants.length === 0 && <p className="pf-hint">No apps yet. Connect one and this agent can use it.</p>}
          {grants.map((g) => {
            const readOnly = g.capability === 'read';
            return (
              <div className="pf-tool" key={g.connectorId}>
                <span className="app-logo fallback" aria-hidden="true">{appName(g.connectorId).charAt(0).toUpperCase()}</span>
                <span className="pf-tool-name"><strong>{appName(g.connectorId)}</strong>
                  <small>{readOnly ? 'Read only' : 'Reads freely, asks before it changes anything'}</small></span>
                <span className="pf-seg" role="radiogroup" aria-label={`${appName(g.connectorId)} access`}>
                  <button type="button" role="radio" aria-checked={!readOnly} className={!readOnly ? 'on' : ''}
                          onClick={() => readOnly && run(() => api.setGrant(agent.agentId, g.connectorId, { capability: 'admin' }))}>Full</button>
                  <button type="button" role="radio" aria-checked={readOnly} className={readOnly ? 'on' : ''}
                          onClick={() => !readOnly && run(() => api.setGrant(agent.agentId, g.connectorId, { capability: 'read' }))}>Read</button>
                </span>
                <button type="button" className="pf-x" aria-label={`Remove ${appName(g.connectorId)}`}
                        onClick={() => run(() => api.removeGrant(agent.agentId, g.connectorId))}><Icon name="x" size={15} /></button>
              </div>
            );
          })}
          <button type="button" className="pf-add" onClick={() => setAdding(true)}><Icon name="plus" size={17} />Add tool</button>

          <h4 className="pf-sub">Built in</h4>
          <div className="pf-chips">
            {(agent.allowedTools || []).map((t) => <span key={t}>{BUILT_IN[t] || t}</span>)}
            {!(agent.allowedTools || []).length && <span>None</span>}
          </div>

          <h4 className="pf-sub">Skills</h4>
          <Skills agent={agent} onChange={onChange} />
        </Section>

        <Section icon="shield" title="Permissions" summary={pre.length ? `${pre.length} pre-approved` : 'Asks before it changes anything'}
                 open={open === 'permissions'} onToggle={() => toggle('permissions')}>
          <p className="pf-hint">Anything that creates, changes or removes data waits for you, unless you pre-approve that one action for this agent.</p>
          {pre.length === 0 && <p className="pf-none">Nothing is pre-approved.</p>}
          {pre.map((t) => (
            <div className="pf-tool" key={t}>
              <Icon name="bolt" size={17} />
              <span className="pf-tool-name"><strong>{t}</strong></span>
              <button type="button" className="pf-x" aria-label={`Stop pre-approving ${t}`}
                      onClick={() => run(() => api.updateAgent(agent.agentId, { preapproved: pre.filter((x) => x !== t) }))}>
                <Icon name="x" size={15} />
              </button>
            </div>
          ))}
        </Section>

        <Section icon="bolt" title="Autonomy" summary="What it does on its own" open={open === 'autonomy'} onToggle={() => toggle('autonomy')}>
          <div className="pf-auto">
            <div><strong>Acts on its own</strong><span>Reading, searching, looking things up, drafting for you to read.</span></div>
            <div><strong>Asks first</strong><span>Sending, posting, editing or deleting anything, spending money, changing settings. Destructive actions can never be pre-approved.</span></div>
            <div><strong>Never allowed</strong><span>Organization admin, cloud administrator credentials and turning off the audit trail, for any agent, whoever asks.</span></div>
          </div>
        </Section>

        <Section icon="layers" title="Memory" summary="What it knows about you" open={open === 'memory'} onToggle={() => toggle('memory')}>
          <Memory agent={agent} onChange={onChange} />
        </Section>

        <Section icon="history" title="Activity" summary="What it has done and spent" open={open === 'activity'} onToggle={() => toggle('activity')}>
          <Activity threadId={threadId} agents={agents} />
          <h4 className="pf-sub">This month</h4>
          <Usage agent={agent} />
        </Section>

        <button type="button" className="pf-more" onClick={() => { onClose(); navigate(`/agents/${agent.agentId}/settings`); }}>
          <span className="sx-icon"><Icon name="settings" size={19} /></span>
          <span className="sx-text"><strong>More settings</strong><small>Spending limits, working hours, pause or archive</small></span>
          <Icon name="forward" size={16} />
        </button>
      </div>
    </Sheet>
  );
}
