import Companion from '../../characters/Companion';

/**
 * The hero product composition: a lightweight, self-contained recreation of
 * the AmazAI workspace. It deliberately does NOT import the live app screens
 * (Room/Sidebar/Composer) — those depend on api.js and auth and would break
 * outside the app. Instead this recreates their layout vocabulary as static
 * marketing markup so the homepage can show a real-feeling product without a
 * backend.
 *
 * "Alive" is carried by a few CSS-only touches — a companion in `working`
 * state, an activity line sliding in, an artifact assembling — never by
 * scroll-jacking or parallax, and all of it is stilled under
 * prefers-reduced-motion (see styles.css).
 */
export default function ProductFrame() {
  return (
    <div className="pf" role="img"
         aria-label="The AmazAI workspace: a sidebar of rooms, a growth room with people and AI companions, a composer, live companion activity, and a Weekly Growth Brief being created.">
      <div className="pf-chrome" aria-hidden="true">
        <span className="pf-dot" /><span className="pf-dot" /><span className="pf-dot" />
        <span className="pf-url">amazai.app / growth</span>
      </div>

      <div className="pf-body">
        {/* --- sidebar: workspace + rooms --- */}
        <aside className="pf-side" aria-hidden="true">
          <div className="pf-workspace">
            <span className="pf-ws-badge">N</span>
            <span className="pf-ws-name">Northwind</span>
          </div>
          <p className="pf-side-label">Rooms</p>
          <ul className="pf-rooms">
            <li className="pf-room is-active"># Growth</li>
            <li className="pf-room"># Product</li>
            <li className="pf-room"># Support</li>
            <li className="pf-room"># Operations</li>
          </ul>
          <p className="pf-side-label">Companions</p>
          <ul className="pf-side-cast">
            <li><Companion archetype="paper" color="#2b6bff" state="working" size={22} decorative /> Marketing</li>
            <li><Companion archetype="moth" color="#e93d82" state="thinking" size={22} decorative /> Research</li>
            <li><Companion archetype="jelly" color="#12a594" state="idle" size={22} decorative /> Operations</li>
          </ul>
        </aside>

        {/* --- main: the room --- */}
        <div className="pf-main">
          <div className="pf-room-head" aria-hidden="true">
            {/* Decorative label inside an aria-hidden product frame, not a
                real section heading: a <span> keeps it out of the page's
                heading outline. */}
            <span className="pf-room-title"># Growth</span>
            <div className="pf-avatars">
              <Companion archetype="paper" color="#2b6bff" state="working" size={30} decorative />
              <Companion archetype="moth" color="#e93d82" state="thinking" size={30} decorative />
              <Companion archetype="jelly" color="#12a594" state="idle" size={30} decorative />
              <span className="pf-person" title="You">AM</span>
            </div>
          </div>

          {/* live companion activity */}
          <div className="pf-feed" aria-hidden="true">
            <div className="pf-msg pf-you">
              <span className="pf-person sm">AM</span>
              <p>Prepare our Monday growth meeting.</p>
            </div>
            <div className="pf-activity pf-a1">
              <Companion archetype="paper" color="#2b6bff" state="working" size={20} decorative />
              <span><b>Marketing</b> is pulling campaign performance…</span>
            </div>
            <div className="pf-activity pf-a2">
              <Companion archetype="moth" color="#e93d82" state="thinking" size={20} decorative />
              <span><b>Research</b> found 3 competitor changes</span>
            </div>

            {/* artifact creation */}
            <div className="pf-artifact">
              <div className="pf-art-icon" aria-hidden="true" />
              <div className="pf-art-meta">
                <span className="pf-art-title">Weekly Growth Brief</span>
                <span className="pf-art-sub">Document · assembling</span>
                <span className="pf-art-bar"><i /></span>
              </div>
            </div>
          </div>

          {/* composer */}
          <div className="pf-composer" aria-hidden="true">
            <span className="pf-composer-ph">Ask your companions to get something done…</span>
            <span className="pf-send" />
          </div>
        </div>
      </div>
    </div>
  );
}
