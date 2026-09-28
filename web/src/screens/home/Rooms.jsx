import Companion from '../../characters/Companion';

/**
 * "Give work a place to live." Alternating section (art on the left this
 * time). A static recreation of a room — informed by Room.jsx/RoomInfo.jsx —
 * showing the six things a room holds: people, companions, messages, files,
 * routines, and artifacts.
 */
export default function Rooms() {
  return (
    <section className="mkt-section mkt-feature mkt-feature-alt" id="rooms">
      <div className="mkt-container mkt-feature-grid">
        <div className="mkt-feature-art">
          <div className="pf-room">
            <div className="pf-room-bar" aria-hidden="true">
              <span className="pf-room-name"># Growth</span>
              <div className="pf-avatars">
                <span className="pf-person sm">AM</span>
                <span className="pf-person sm">JP</span>
                <Companion archetype="paper" color="#2b6bff" state="idle" size={24} decorative />
                <Companion archetype="moth" color="#e93d82" state="working" size={24} decorative />
              </div>
            </div>
            <div className="pf-room-cols" aria-hidden="true">
              <div className="pf-room-thread">
                <div className="pf-msg pf-you"><span className="pf-person sm">JP</span><p>How did the launch land?</p></div>
                <div className="pf-activity">
                  <Companion archetype="paper" color="#2b6bff" state="working" size={18} decorative />
                  <span><b>Marketing</b> posted the recap</span>
                </div>
              </div>
              <div className="pf-room-rail">
                <p className="pf-rail-label">Files</p>
                <span className="pf-rail-chip">launch-metrics.csv</span>
                <p className="pf-rail-label">Routines</p>
                <span className="pf-rail-chip">Weekly recap · Mon</span>
                <p className="pf-rail-label">Artifacts</p>
                <span className="pf-rail-chip">Growth Brief</span>
              </div>
            </div>
          </div>
        </div>

        <div className="mkt-feature-copy">
          <p className="mkt-eyebrow">Rooms</p>
          <h2 className="mkt-title">Give work a place to live.</h2>
          <p className="mkt-lede">
            A room is where a piece of work happens. People and companions
            share the same space, alongside the messages, files, routines, and
            artifacts that belong to it — so context never scatters across a
            dozen chat threads.
          </p>
          <ul className="mkt-checklist">
            <li>People and companions, side by side</li>
            <li>Messages, files, and shared context</li>
            <li>Routines and artifacts kept in one place</li>
          </ul>
        </div>
      </div>
    </section>
  );
}
