import Companion from '../characters/Companion';

/**
 * A room's face: its members set inside one round tile, side by side.
 *
 * Overlapped avatars need a ring in the background colour to separate them, and that
 * ring reads as a small circle biting into its neighbour -- on any surface that is not
 * exactly that colour (a selected row, the floating header) it shows as a dark
 * outline. A tile has nothing to overlap: one member fills it, two sit in a row, three
 * make a triangle, four a square, and any more become three and a "+N".
 */
export default function GroupMark({ members = [], size = 52, states = {} }) {
  const many = members.length > 4;
  const shown = many ? members.slice(0, 3) : members.slice(0, 4);
  const extra = many ? members.length - 3 : 0;
  const n = shown.length + (extra ? 1 : 0);
  const face = Math.round(size * (n <= 1 ? 0.72 : 0.4));

  return (
    <span className="gm" data-n={n} style={{ '--gm': `${size}px` }} aria-hidden="true">
      {shown.length === 0 && <Companion archetype="pebble" color="#8a8f9c" state="offline" size={face} decorative />}
      {shown.map((m) => (
        <span className="gm-cell" key={m.agentId}>
          <Companion archetype={m.archetype} color={m.color} state={states[m.agentId] || m.state} size={face} decorative />
        </span>
      ))}
      {extra > 0 && <span className="gm-cell gm-more">+{extra}</span>}
    </span>
  );
}
