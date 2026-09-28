import Companion from '../../characters/Companion';

/**
 * "Build the team you need." First of the alternating product sections. It
 * introduces the *companion* concept with a static profile frame (informed by
 * AgentProfile) beside one shown actively working, so the vocabulary lands
 * against real product UI rather than a bullet list.
 */
export default function Companions() {
  return (
    <section className="mkt-section mkt-feature" id="companions">
      <div className="mkt-container mkt-feature-grid">
        <div className="mkt-feature-copy">
          <p className="mkt-eyebrow">Companions</p>
          <h2 className="mkt-title">Build the team you need.</h2>
          <p className="mkt-lede">
            A companion is more than a prompt. Give it a role, standing
            instructions, the tools it&rsquo;s allowed to use, the context it
            works from, and ongoing responsibility — then it keeps carrying
            that work, run after run.
          </p>
          <ul className="mkt-checklist">
            <li>Roles and clear instructions</li>
            <li>Scoped tools and connectors</li>
            <li>Context it remembers between runs</li>
            <li>Ongoing responsibility, not one-off replies</li>
          </ul>
        </div>

        <div className="mkt-feature-art">
          <div className="pf-profile">
            <div className="pf-profile-top">
              <Companion archetype="moth" color="#e93d82" state="working" size={64} name="Research Companion" />
              <div>
                <span className="pf-profile-name">Research Companion</span>
                <span className="pf-profile-role">Market &amp; competitor research</span>
              </div>
            </div>
            <dl className="pf-profile-meta">
              <div><dt>Tools</dt><dd>Web search · Drive · Slack</dd></div>
              <div><dt>Context</dt><dd>Northwind positioning doc</dd></div>
              <div><dt>Responsibility</dt><dd>Weekly competitive brief</dd></div>
            </dl>
            <div className="pf-profile-now" aria-hidden="true">
              <Companion archetype="moth" color="#e93d82" state="working" size={20} decorative />
              <span>Comparing pricing pages across 4 competitors…</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
