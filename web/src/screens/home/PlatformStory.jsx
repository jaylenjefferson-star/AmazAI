import Companion from '../../characters/Companion';
import useReveal from './useReveal';

/**
 * The narrative pivot: from "AI answers" to "AI does the work". A single
 * example request fans out to three companions, produces three durable
 * outputs, and becomes a scheduled routine — the whole multi-agent idea
 * without a word of architecture.
 *
 * Motion is a staggered CSS entrance keyed off an IntersectionObserver (see
 * useReveal); it demonstrates delegation appearing, not decoration. The final
 * layout is fully visible with motion disabled.
 */
const DELEGATES = [
  { name: 'Marketing Companion', task: 'Pulls campaign performance', archetype: 'paper', color: '#2b6bff', state: 'working' },
  { name: 'Research Companion', task: 'Finds competitor changes', archetype: 'moth', color: '#e93d82', state: 'thinking' },
  { name: 'Operations Companion', task: 'Reviews relevant business metrics', archetype: 'jelly', color: '#12a594', state: 'working' },
];

const OUTPUTS = ['Weekly Growth Brief', 'Competitive Update', 'Meeting Agenda'];

export default function PlatformStory() {
  const [ref, shown] = useReveal();
  return (
    <section className="mkt-section mkt-story" id="platform">
      <div className="mkt-container">
        <div className="mkt-story-head">
          <p className="mkt-eyebrow">AI shouldn&rsquo;t just answer questions.</p>
          <h2 className="mkt-title mkt-story-title">It should get the work done.</h2>
        </div>

        <div ref={ref} className={`mkt-flow${shown ? ' is-in' : ''}`}>
          <div className="mkt-flow-request">
            <span className="mkt-flow-you">You</span>
            <p>&ldquo;Prepare our Monday growth meeting.&rdquo;</p>
          </div>

          <p className="mkt-flow-caption">Work is delegated</p>
          <ol className="mkt-flow-delegates">
            {DELEGATES.map((d, i) => (
              <li key={d.name} className="mkt-flow-agent" style={{ '--i': i }}>
                <Companion archetype={d.archetype} color={d.color} state={d.state} size={40} decorative />
                <div>
                  <span className="mkt-flow-agent-name">{d.name}</span>
                  <span className="mkt-flow-agent-task">{d.task}</span>
                </div>
              </li>
            ))}
          </ol>

          <p className="mkt-flow-caption">Finished work comes back</p>
          <ul className="mkt-flow-outputs">
            {OUTPUTS.map((o, i) => (
              <li key={o} className="mkt-flow-output" style={{ '--i': i }}>
                <span className="mkt-flow-output-icon" aria-hidden="true" />
                {o}
              </li>
            ))}
          </ul>

          <div className="mkt-flow-routine">
            <span className="mkt-flow-clock" aria-hidden="true" />
            Routine scheduled for next Monday
          </div>
        </div>
      </div>
    </section>
  );
}
