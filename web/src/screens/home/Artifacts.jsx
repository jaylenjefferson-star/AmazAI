/**
 * "Finished work, not disappearing chat responses." The strongest product
 * moment: a beautiful artifact library beside an artifact viewer (informed by
 * Sections.jsx Artifacts). Companions produce durable, openable outputs —
 * documents, research, spreadsheets, plans, reports, images, code, and other
 * files — not messages that scroll away.
 */
const LIBRARY = [
  { title: 'Weekly Growth Brief', kind: 'Document', tone: 'doc', active: true },
  { title: 'Competitor Landscape', kind: 'Research', tone: 'research' },
  { title: 'Q3 Revenue Model', kind: 'Spreadsheet', tone: 'sheet' },
  { title: 'Launch Plan', kind: 'Plan', tone: 'plan' },
  { title: 'Support Trends Report', kind: 'Report', tone: 'report' },
  { title: 'Hero Illustration', kind: 'Image', tone: 'image' },
  { title: 'checkout-fix.patch', kind: 'Code', tone: 'code' },
  { title: 'Pricing Update', kind: 'Document', tone: 'doc' },
];

export default function Artifacts() {
  return (
    <section className="mkt-section mkt-artifacts" id="artifacts">
      <div className="mkt-container">
        <div className="mkt-artifacts-head">
          <p className="mkt-eyebrow">Artifacts</p>
          <h2 className="mkt-title">Finished work, not disappearing chat responses.</h2>
          <p className="mkt-lede">
            Companions create durable outputs you can open, edit, and share:
            documents, research, spreadsheets, plans, reports, images, code,
            and other files. Every result has a home — nothing gets lost in a
            scrollback.
          </p>
        </div>

        <div className="mkt-artifacts-stage">
          <div className="pf-lib" aria-label="Artifact library" role="img">
            <div className="pf-lib-head" aria-hidden="true">
              <span>Artifacts</span>
              <span className="pf-lib-count">8</span>
            </div>
            <ul className="pf-lib-grid" aria-hidden="true">
              {LIBRARY.map((a) => (
                <li key={a.title} className={`pf-lib-item${a.active ? ' is-active' : ''}`}>
                  <span className={`pf-lib-icon tone-${a.tone}`} aria-hidden="true" />
                  <span className="pf-lib-title">{a.title}</span>
                  <span className="pf-lib-kind">{a.kind}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="pf-viewer" aria-label="Weekly Growth Brief, an artifact created by a companion" role="img">
            <div className="pf-viewer-bar" aria-hidden="true">
              <span className="pf-viewer-title">Weekly Growth Brief</span>
              <span className="pf-viewer-meta">Document · created by Marketing Companion</span>
            </div>
            <div className="pf-viewer-doc" aria-hidden="true">
              <h4>Weekly Growth Brief</h4>
              <p className="pf-doc-lead">Signups grew 18% week over week, led by the launch campaign.</p>
              <p className="pf-doc-h">Highlights</p>
              <span className="pf-doc-line" />
              <span className="pf-doc-line short" />
              <span className="pf-doc-line" />
              <p className="pf-doc-h">Competitive changes</p>
              <span className="pf-doc-line" />
              <span className="pf-doc-line short" />
              <div className="pf-doc-chart" aria-hidden="true">
                <i style={{ height: '38%' }} /><i style={{ height: '52%' }} />
                <i style={{ height: '46%' }} /><i style={{ height: '71%' }} />
                <i style={{ height: '64%' }} /><i style={{ height: '88%' }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
