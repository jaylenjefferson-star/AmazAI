import Companion from '../characters/Companion';

/**
 * A room's face: its members set inside one round tile, never overlapped.
 *
 * Overlapped avatars need a ring in the background colour to separate them, and that
 * ring reads as a small circle biting into its neighbour -- on any surface that is not
 * exactly that colour (a selected row, the floating header) it shows as a dark outline.
 * A tile has nothing to overlap.
 *
 * The faces sit on a ring inside the circle, evenly spaced, each sized so that
 *   - neighbours are at least a face apart (they cannot touch), and
 *   - the outermost edge of every face is on or inside the tile (nothing is clipped).
 * For n faces of diameter d on a ring of radius r (all as a fraction of the tile):
 *   r = 0.5 - d/2 keeps them inside; neighbours are 2r*sin(pi/n) apart, which must be >= d.
 * That gives d = 0.44 for two, 0.42 for three and 0.40 for four. One member fills the tile.
 * More than four is three faces and a "+N" in the fourth place.
 */
const LAYOUT = {
  1: { d: 0.74, angles: [90] },                       // one, centred (r is 0)
  2: { d: 0.44, angles: [135, 315] },                 // a diagonal pair
  3: { d: 0.42, angles: [90, 210, 330] },             // a triangle
  4: { d: 0.40, angles: [135, 45, 225, 315] },        // a square
};

export default function GroupMark({ members = [], size = 52, states = {} }) {
  const many = members.length > 4;
  const shown = many ? members.slice(0, 3) : members.slice(0, 4);
  const extra = many ? members.length - 3 : 0;
  const n = shown.length + (extra ? 1 : 0);
  const { d, angles } = LAYOUT[Math.max(1, Math.min(4, n))];
  const r = n === 1 ? 0 : 0.5 - d / 2;
  const at = (i) => {
    const a = (angles[i] * Math.PI) / 180;
    const cx = 0.5 + r * Math.cos(a);
    const cy = 0.5 - r * Math.sin(a);
    return { left: `${(cx - d / 2) * size}px`, top: `${(cy - d / 2) * size}px`, width: `${d * size}px`, height: `${d * size}px` };
  };
  const face = Math.round(d * size);

  return (
    <span className="gm" data-n={n} style={{ '--gm': `${size}px` }} aria-hidden="true">
      {shown.length === 0 && (
        <span className="gm-cell" style={at(0)}>
          <Companion archetype="pebble" color="#8a8f9c" state="offline" size={face} decorative />
        </span>
      )}
      {shown.map((m, i) => (
        <span className="gm-cell" key={m.agentId} style={at(i)}>
          <Companion archetype={m.archetype} color={m.color} state={states[m.agentId] || m.state} size={face} decorative />
        </span>
      ))}
      {extra > 0 && <span className="gm-cell gm-more" style={at(shown.length)}>+{extra}</span>}
    </span>
  );
}
