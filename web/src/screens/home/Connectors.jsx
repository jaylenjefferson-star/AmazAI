import useReveal from './useReveal';

/**
 * "Work where your work already lives." The connectors section pairs the
 * same honest integration set the trust strip established (only Slack is real
 * today; the rest are labelled Upcoming) with a small workflow that visibly
 * crosses several of those tools — so the point is not "we have logos", it is
 * "a single piece of work moves across the tools you already use".
 *
 * The workflow steps reveal in a short stagger keyed off useReveal; the final
 * layout is fully legible with motion off. id=connectors so the Product
 * mega-menu's Connectors link resolves to this section.
 */
const TOOLS = [
  { name: 'Slack', upcoming: false },
  { name: 'GitHub', upcoming: true },
  { name: 'Google Drive', upcoming: true },
  { name: 'Gmail', upcoming: true },
  { name: 'Google Calendar', upcoming: true },
  { name: 'Notion', upcoming: true },
  { name: 'Microsoft', upcoming: true },
];

// A single task crossing several tools, in the order it would actually run.
const FLOW = [
  { tool: 'Google Drive', action: 'Read the campaign brief' },
  { tool: 'GitHub', action: 'Checked the release notes' },
  { tool: 'Notion', action: 'Updated the launch page' },
  { tool: 'Slack', action: 'Posted the summary to #launch' },
];

export default function Connectors() {
  const [ref, shown] = useReveal();
  return (
    <section className="mkt-section mkt-connectors" id="connectors">
      <div className="mkt-container">
        <div className="mkt-connectors-head">
          <p className="mkt-eyebrow">Connectors</p>
          <h2 className="mkt-title">Work where your work already lives.</h2>
          <p className="mkt-lede">
            Companions reach into the tools your team already uses, so work
            moves between them without copy-and-paste. Connect a service once
            and every companion can pick up where it left off.
          </p>
        </div>

        <div className="mkt-connectors-stage">
          <ul className="mkt-conn-tools" aria-label="Connected services">
            {TOOLS.map((t) => (
              <li key={t.name} className={`mkt-conn-tool${t.upcoming ? ' is-upcoming' : ''}`}>
                <span className="mkt-conn-tool-dot" aria-hidden="true" />
                <span className="mkt-conn-tool-name">{t.name}</span>
                {t.upcoming && <span className="mkt-conn-tag">Upcoming</span>}
              </li>
            ))}
          </ul>

          <div ref={ref} className={`mkt-conn-flow${shown ? ' is-in' : ''}`}>
            <p className="mkt-conn-flow-cap">One task, across your tools</p>
            <ol className="mkt-conn-steps" aria-label="A workflow crossing several tools">
              {FLOW.map((s, i) => (
                <li key={s.tool} className="mkt-conn-step" style={{ '--i': i }}>
                  <span className="mkt-conn-step-tool">{s.tool}</span>
                  <span className="mkt-conn-step-action">{s.action}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}
