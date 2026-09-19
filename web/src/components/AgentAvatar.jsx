/**
 * An agent's face.
 *
 * Shape and colour together are how an agent is recognised where there is no
 * room for its name — a handoff line, a collab fold, a dense sidebar. Colour
 * alone would not survive a colour-blind reader, which is why the shape
 * varies too rather than being decoration on top of a palette.
 *
 * The vocabulary comes from the API (`GET /agents/options`), which serves the
 * same constants the validator enforces. Nothing here invents a shape.
 */

const PATHS = {
  circle:   'M50 6a44 44 0 1 0 .1 0Z',
  squircle: 'M50 6c33 0 44 11 44 44s-11 44-44 44S6 83 6 50 17 6 50 6Z',
  square:   'M18 8h64a10 10 0 0 1 10 10v64a10 10 0 0 1-10 10H18A10 10 0 0 1 8 82V18A10 10 0 0 1 18 8Z',
  pill:     'M30 22h40a28 28 0 0 1 0 56H30a28 28 0 0 1 0-56Z',
  triangle: 'M50 10 92 84a8 8 0 0 1-7 12H15a8 8 0 0 1-7-12Z',
  hex:      'M50 5 88 27a8 8 0 0 1 4 7v32a8 8 0 0 1-4 7L50 95a8 8 0 0 1-8 0L12 73a8 8 0 0 1-4-7V34a8 8 0 0 1 4-7L42 5a8 8 0 0 1 8 0Z',
  cloud:    'M31 84a25 25 0 0 1-2-50 22 22 0 0 1 41-6 21 21 0 0 1 2 56Z',
  drop:     'M50 6c18 26 30 38 30 54a30 30 0 0 1-60 0c0-16 12-28 30-54Z',
};

export default function AgentAvatar({ shape = 'circle', color = '#2f6fe4',
                                      size = 24, name = '', title }) {
  const d = PATHS[shape] || PATHS.circle;
  // Eyes are placed for the round shapes and read fine on the angular ones;
  // the triangle is the exception, where they sit lower to stay inside it.
  const eyeY = shape === 'triangle' ? 62 : 46;

  return (
    <svg className="avatar" width={size} height={size} viewBox="0 0 100 100"
         role="img" aria-label={title || name || 'agent'}>
      {title || name ? <title>{title || name}</title> : null}
      <path d={d} fill={color} />
      <g fill="rgba(0,0,0,.72)">
        <rect x="36" y={eyeY} width="9" height="20" rx="4.5" />
        <rect x="55" y={eyeY} width="9" height="20" rx="4.5" />
      </g>
    </svg>
  );
}

export { PATHS as AVATAR_PATHS };
