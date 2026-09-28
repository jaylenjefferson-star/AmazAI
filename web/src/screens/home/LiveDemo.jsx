import Companion from '../../characters/Companion';
import useReveal from './useReveal';

/**
 * Live product demonstration inside a browser-chrome frame. A real request is
 * typed, then companions do the work in front of you — quiet, product-like
 * activity lines rather than loud status badges. The past-tense verbs
 * (Searched / Read / Analyzed / Created / Updated / Messaged) are the spec's
 * activity vocabulary: they read as work performed, not chat returned.
 *
 * The activity stream reveals in a short stagger keyed off useReveal so it
 * looks like the work unfolding; the final state is fully legible with motion
 * off (see the prefers-reduced-motion block in styles.css).
 */
const ACTIVITY = [
  {
    who: 'Research Companion', archetype: 'moth', color: '#e93d82', state: 'working',
    status: 'Searching the web',
    lines: [
      { verb: 'Searched', detail: 'competitor newsrooms and blogs' },
      { verb: 'Read', detail: '18 sources reviewed' },
      { verb: 'Analyzed', detail: 'what changed this week' },
    ],
  },
  {
    who: 'Marketing Companion', archetype: 'paper', color: '#2b6bff', state: 'thinking',
    status: 'Reviewing findings',
    lines: [
      { verb: 'Created', artifact: true, title: 'Competitive Brief', meta: 'Sep 27' },
      { verb: 'Messaged', channel: '#marketing' },
    ],
  },
];

export default function LiveDemo() {
  const [ref, shown] = useReveal();
  return (
    <section className="mkt-section mkt-demo" id="live-demo">
      <div className="mkt-container">
        <div className="mkt-demo-head">
          <p className="mkt-eyebrow">See it work</p>
          <h2 className="mkt-title">Ask once. Watch the work happen.</h2>
          <p className="mkt-lede">
            You describe the outcome. Your companions search, read, analyze,
            and produce finished work — and you can watch every step as it
            happens.
          </p>
        </div>

        <div ref={ref} className={`pf pf-demo${shown ? ' is-in' : ''}`}>
          <div className="pf-chrome" aria-hidden="true">
            <span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" />
            <span className="pf-url">app.amazai.co/rooms/growth</span>
          </div>

          <div className="pf-demo-body">
            <div className="pf-demo-request">
              <span className="pf-demo-you">You</span>
              <p>
                Find our three biggest competitor announcements this week and
                tell the marketing team what we should react to.
              </p>
            </div>

            <ol className="pf-demo-stream" aria-label="Companion activity">
              {ACTIVITY.map((a, ai) => (
                <li key={a.who} className="pf-demo-agent" style={{ '--i': ai }}>
                  <div className="pf-demo-agent-head">
                    <Companion archetype={a.archetype} color={a.color} state={a.state} size={26} decorative />
                    <span className="pf-demo-agent-name">{a.who}</span>
                    <span className="pf-demo-agent-status">{a.status}</span>
                  </div>
                  <ul className="pf-demo-acts">
                    {a.lines.map((l, li) => (
                      <li key={li} className="pf-demo-act">
                        {l.artifact ? (
                          <span className="pf-demo-artifact">
                            <span className="pf-demo-art-icon" aria-hidden="true" />
                            <span className="pf-demo-art-text">
                              <span className="pf-demo-verb">Artifact created</span>
                              <span className="pf-demo-art-title">{l.title} · {l.meta}</span>
                            </span>
                          </span>
                        ) : (
                          <>
                            <span className="pf-demo-verb">{l.verb}</span>
                            <span className="pf-demo-detail">{l.channel || l.detail}</span>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
}
