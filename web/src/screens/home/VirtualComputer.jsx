import Companion from '../../characters/Companion';
import useReveal from './useReveal';

/**
 * The core differentiator: a companion with its own computer. Left is a
 * browser/application view (a competitor pricing page it opened); right is the
 * companion's task and a quiet activity stream. The section deliberately says
 * nothing about *how* the computer runs — no infrastructure vocabulary — only
 * what a companion can do with one: use browsers, tools, files, and apps to
 * finish multi-step work.
 *
 * id=browser-computer so the Product mega-menu's "Browser & Computer" link
 * resolves here.
 */
const ACTIVITY = [
  { verb: 'Opened website', done: true },
  { verb: 'Read pricing', done: true },
  { verb: 'Captured changes', done: true },
  { verb: 'Updating pricing analysis', done: false },
];

export default function VirtualComputer() {
  const [ref, shown] = useReveal();
  return (
    <section className="mkt-section mkt-computer" id="browser-computer">
      <div className="mkt-container">
        <div className="mkt-computer-head">
          <p className="mkt-eyebrow">Not limited to a chat box.</p>
          <h2 className="mkt-computer-title">Give your AI a computer.</h2>
          <p className="mkt-lede">
            Your companions can use browsers, tools, files, and applications to
            complete multi-step work, not just tell you how to do it.
          </p>
        </div>

        <div ref={ref} className={`mkt-computer-stage${shown ? ' is-in' : ''}`}>
          <div className="pf pf-screen" aria-label="A browser a companion is using" role="img">
            <div className="pf-chrome" aria-hidden="true">
              <span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" />
              <span className="pf-url">competitor.example.com/pricing</span>
            </div>
            <div className="pf-screen-page" aria-hidden="true">
              <div className="pf-screen-nav">
                <span className="pf-screen-logo" />
                <span className="pf-screen-navline" />
                <span className="pf-screen-navline short" />
              </div>
              <span className="pf-screen-h" />
              <div className="pf-screen-plans">
                {['$0', '$29', '$79'].map((p) => (
                  <div key={p} className="pf-screen-plan">
                    <span className="pf-screen-price">{p}</span>
                    <span className="pf-screen-planline" />
                    <span className="pf-screen-planline short" />
                    <span className="pf-screen-planline" />
                  </div>
                ))}
              </div>
              {/* the companion's "cursor" resting on the plan it is reading */}
              <span className="pf-screen-cursor" />
            </div>
          </div>

          <aside className="pf pf-task" aria-label="Maya's computer, working">
            <div className="pf-task-head">
              <Companion archetype="jelly" color="#12a594" state="working" size={30} name="Maya" />
              <div>
                <span className="pf-task-owner">Maya&rsquo;s computer</span>
                <span className="pf-task-state">Working</span>
              </div>
            </div>
            <div className="pf-task-body">
              <p className="pf-task-label">Task</p>
              <p className="pf-task-desc">Compare competitor pricing against our current plans.</p>
              <p className="pf-task-label">Activity</p>
              <ol className="pf-task-acts">
                {ACTIVITY.map((a, i) => (
                  <li key={a.verb} className={`pf-task-act${a.done ? ' is-done' : ' is-now'}`} style={{ '--i': i }}>
                    <span className="pf-task-tick" aria-hidden="true" />
                    <span>{a.verb}</span>
                  </li>
                ))}
              </ol>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}
