import { useRef, useState } from 'react';
import Companion from '../../characters/Companion';

/**
 * "Built for the work happening across your company" — one tablist, six teams.
 * Selecting a team swaps the copy, the example prompt, the mini-workflow, and
 * the adjacent product preview. Descriptions are verbatim from the spec.
 *
 * Accessibility: a real ARIA tablist with roving focus. Left/Right (and
 * Home/End) move between tabs and activate on move (the common "automatic
 * activation" pattern), matching the WAI-ARIA tabs guidance. The strip
 * scrolls horizontally on narrow screens instead of wrapping into overflow.
 */
const TEAMS = [
  {
    key: 'founders', label: 'Founders',
    copy: 'Research markets, prepare meetings, track priorities, and keep projects moving.',
    prompt: 'Summarize this week and prep the board update.',
    flow: ['Research the market', 'Prepare the meeting', 'Track priorities'],
    archetype: 'pebble', color: '#8b2fe0',
    preview: { title: 'Board Update', sub: 'Document · ready' },
  },
  {
    key: 'marketing', label: 'Marketing',
    copy: 'Research competitors, create campaigns, repurpose content, and monitor performance.',
    prompt: 'Turn last week\u2019s launch into a campaign.',
    flow: ['Research competitors', 'Create the campaign', 'Repurpose content'],
    archetype: 'paper', color: '#2b6bff',
    preview: { title: 'Launch Campaign', sub: 'Plan · 6 assets' },
  },
  {
    key: 'sales', label: 'Sales',
    copy: 'Research accounts, prepare calls, draft follow-ups, and maintain pipeline context.',
    prompt: 'Prep me for the Acme call at 2 pm.',
    flow: ['Research the account', 'Prepare the call', 'Draft follow-ups'],
    archetype: 'lantern', color: '#e8833a',
    preview: { title: 'Acme Call Brief', sub: 'Document · ready' },
  },
  {
    key: 'operations', label: 'Operations',
    copy: 'Automate recurring workflows and move information between systems.',
    prompt: 'Reconcile this week\u2019s orders every Friday.',
    flow: ['Automate the workflow', 'Move the information', 'Notify the team'],
    archetype: 'jelly', color: '#12a594',
    preview: { title: 'Weekly Reconciliation', sub: 'Routine · Fridays' },
  },
  {
    key: 'product', label: 'Product',
    copy: 'Synthesize feedback, create specs, research competitors, and monitor releases.',
    prompt: 'Synthesize this month\u2019s feedback into themes.',
    flow: ['Synthesize feedback', 'Create the spec', 'Monitor releases'],
    archetype: 'cloud', color: '#0ea5e9',
    preview: { title: 'Feedback Themes', sub: 'Research · 5 themes' },
  },
  {
    key: 'engineering', label: 'Engineering',
    copy: 'Investigate issues, research repositories, create implementation plans, and coordinate technical work.',
    prompt: 'Investigate the checkout error and plan a fix.',
    flow: ['Investigate the issue', 'Research the repo', 'Create a plan'],
    archetype: 'moth', color: '#e93d82',
    preview: { title: 'Implementation Plan', sub: 'Document · draft' },
  },
];

export default function TeamUseCases() {
  const [active, setActive] = useState(0);
  const tabsRef = useRef([]);

  const move = (next) => {
    const i = (next + TEAMS.length) % TEAMS.length;
    setActive(i);
    tabsRef.current[i]?.focus();
  };

  const onKey = (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); move(active + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); move(active - 1); }
    else if (e.key === 'Home') { e.preventDefault(); move(0); }
    else if (e.key === 'End') { e.preventDefault(); move(TEAMS.length - 1); }
  };

  const t = TEAMS[active];

  return (
    <section className="mkt-section mkt-teams">
      <div className="mkt-container">
        <div className="mkt-teams-head">
          <h2 className="mkt-title">Built for the work happening across your company.</h2>
        </div>

        <div className="mkt-tabs" role="tablist" aria-label="Teams" onKeyDown={onKey}>
          {TEAMS.map((team, i) => (
            <button
              key={team.key}
              ref={(el) => { tabsRef.current[i] = el; }}
              role="tab"
              id={`team-tab-${team.key}`}
              aria-selected={i === active}
              aria-controls={`team-panel-${team.key}`}
              tabIndex={i === active ? 0 : -1}
              className={`mkt-tab${i === active ? ' is-active' : ''}`}
              onClick={() => setActive(i)}
            >
              {team.label}
            </button>
          ))}
        </div>

        <div
          role="tabpanel"
          id={`team-panel-${t.key}`}
          aria-labelledby={`team-tab-${t.key}`}
          className="mkt-teams-panel"
          key={t.key}
        >
          <div className="mkt-teams-copy">
            <p className="mkt-eyebrow">{t.label}</p>
            <p className="mkt-teams-desc">{t.copy}</p>
            <div className="mkt-teams-prompt">
              <span className="mkt-teams-prompt-label">Example</span>
              <span>&ldquo;{t.prompt}&rdquo;</span>
            </div>
            <ol className="mkt-teams-flow">
              {t.flow.map((step) => <li key={step}>{step}</li>)}
            </ol>
          </div>

          <div className="mkt-teams-preview">
            <div className="pf-mini">
              <div className="pf-mini-head">
                <Companion archetype={t.archetype} color={t.color} state="working" size={30} decorative />
                <span>{t.label} Companion</span>
              </div>
              <div className="pf-mini-art" style={{ '--tint': t.color }}>
                <span className="pf-mini-icon" aria-hidden="true" />
                <div>
                  <span className="pf-mini-title">{t.preview.title}</span>
                  <span className="pf-mini-sub">{t.preview.sub}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
