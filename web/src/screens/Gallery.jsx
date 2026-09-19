import { useState } from 'react';
import Companion, { STATES, STATE_KEYS } from '../characters/Companion';
import { ARCHETYPES, ARCHETYPE_KEYS } from '../characters/archetypes';

const PALETTE = ['#2b6bff', '#8b2fe0', '#12a594', '#e8833a', '#e93d82', '#3dc98a'];

/**
 * Every companion, in every state, on one screen.
 *
 * This exists to be looked at. Reviewing seven animated states across six
 * silhouettes by triggering each one in the real app would mean driving a
 * task to failure to see `blocked`, which is a poor way to check whether a
 * tangle reads as a tangle.
 *
 * It is also the reduced-motion check: turn the OS setting on, reload, and
 * every state should still be distinguishable from every other.
 */
export default function Gallery() {
  const [size, setSize] = useState(72);
  const [color, setColor] = useState(PALETTE[0]);
  const [replay, setReplay] = useState(0);

  return (
    <div className="gallery">
      <header className="gallery-head">
        <div>
          <h1>Companions</h1>
          <p>
            Six silhouettes, seven states. Shape carries the identity and the
            label carries the state, so neither depends on colour or motion
            alone.
          </p>
        </div>

        <div className="gallery-controls">
          <label className="field" style={{ margin: 0 }}>
            <span>Accent</span>
            <div className="picker" style={{ justifyContent: 'flex-start', margin: 0 }}>
              {PALETTE.map((c) => (
                <button key={c} type="button" aria-label={c}
                        className={`dot-swatch ${color === c ? 'on' : ''}`}
                        style={{ background: c }} onClick={() => setColor(c)} />
              ))}
            </div>
          </label>

          <label className="field" style={{ margin: 0 }}>
            <span>Size — {size}px</span>
            <input type="range" min="24" max="120" value={size}
                   onChange={(e) => setSize(Number(e.target.value))} />
          </label>

          {/* `complete` and `blocked` play once by design, so they need a way
              to be seen twice. Remounting is the honest way to replay a
              one-shot animation. */}
          <button className="sm" onClick={() => setReplay((n) => n + 1)}>
            Replay one-shots
          </button>
        </div>
      </header>

      <div className="gallery-grid" key={replay}>
        {ARCHETYPE_KEYS.map((key) => (
          <section key={key} className="gallery-row">
            <div className="gallery-who">
              <Companion archetype={key} color={color} state="idle" size={size} />
              <div>
                <h2>{ARCHETYPES[key].name}</h2>
                <p>{ARCHETYPES[key].blurb}</p>
              </div>
            </div>

            <div className="gallery-states">
              {STATE_KEYS.map((state) => (
                <figure key={state} className="gallery-cell">
                  <Companion archetype={key} color={color} state={state}
                             size={size} name={ARCHETYPES[key].name} />
                  <figcaption>
                    <strong>{STATES[state].label}</strong>
                    <span>{STATES[state].verb}</span>
                  </figcaption>
                </figure>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
