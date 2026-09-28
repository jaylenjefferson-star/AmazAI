import Companion from '../../characters/Companion';

/**
 * "Turn repeated work into something that runs itself." Alternating section
 * with a routine configuration card and an execution timeline (informed by
 * Sections.jsx Routines + Timeline.jsx). The example is verbatim from the
 * spec: Competitive Intelligence, every Monday at 8am.
 */
const STEPS = [
  { label: 'Research competitors', state: 'complete' },
  { label: 'Analyze meaningful changes', state: 'complete' },
  { label: 'Update brief', state: 'working' },
  { label: 'Notify Marketing', state: 'waiting' },
];

export default function Routines() {
  return (
    <section className="mkt-section mkt-feature" id="routines">
      <div className="mkt-container mkt-feature-grid">
        <div className="mkt-feature-copy">
          <p className="mkt-eyebrow">Routines</p>
          <h2 className="mkt-title">Turn repeated work into something that runs itself.</h2>
          <p className="mkt-lede">
            Set the work once and the schedule takes it from there. A routine
            runs your companions on a cadence, step by step, and hands you the
            finished result — no one has to remember to kick it off.
          </p>
        </div>

        <div className="mkt-feature-art">
          <div className="pf-routine">
            <div className="pf-routine-head">
              <div>
                <span className="pf-routine-title">Competitive Intelligence</span>
                <span className="pf-routine-cadence">Every Monday · 8:00 AM</span>
              </div>
              <span className="pf-routine-toggle" aria-hidden="true"><i /></span>
            </div>
            <ol className="pf-timeline" aria-hidden="true">
              {STEPS.map((s) => (
                <li key={s.label} className={`pf-tl-step is-${s.state}`}>
                  <span className="pf-tl-dot" />
                  <span className="pf-tl-label">{s.label}</span>
                  <Companion archetype="moth" color="#e93d82" state={s.state} size={18} decorative />
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}
