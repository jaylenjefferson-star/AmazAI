/**
 * The AmazAI mark, drawn rather than loaded.
 *
 * SVG because it has to work at 22px in a topbar and 96px on a login screen,
 * on light and dark, without shipping three PNGs or a network request on
 * first paint. The gradients carry the brand; the geometry is a chevron "A"
 * with a second rounded form overlapping it, which is what makes the mark
 * read as a monogram rather than a triangle.
 *
 * `id` is instance-scoped. Two of these on one page with the same gradient
 * ids would have the second silently adopt the first's fill.
 */

let counter = 0;

export function LogoMark({ size = 28, title }) {
  const uid = `amazai-${(counter += 1)}`;
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 100 100"
         role={title ? 'img' : 'presentation'} aria-label={title || undefined}
         aria-hidden={title ? undefined : 'true'}>
      {title && <title>{title}</title>}
      <defs>
        <linearGradient id={`${uid}-a`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#0EA5F0" />
          <stop offset="55%" stopColor="#2B6BFF" />
          <stop offset="100%" stopColor="#4F46E5" />
        </linearGradient>
        <linearGradient id={`${uid}-b`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#4F2BD8" />
          <stop offset="60%" stopColor="#8B2FE0" />
          <stop offset="100%" stopColor="#C026D3" />
        </linearGradient>
      </defs>

      {/* The left limb, drawn as a round-capped stroke: the caps are the
          shape, so a path with hand-built end curves would only be a longer
          way to say the same thing. */}
      <line x1="49" y1="21" x2="19" y2="73"
            stroke={`url(#${uid}-a)`} strokeWidth="27" strokeLinecap="round" />

      {/* The right form, apex tucked under the limb's upper third so the two
          interlock into an A rather than sitting beside each other. */}
      <path
        d="M48.5 31.5q4-8.5 11.5-4 3 1.8 4.6 5.2l22.4 38.6q5.6 10.7-6.4 10.7H44.4q-12 0-6.4-10.7Z"
        fill={`url(#${uid}-b)`}
      />

      {/* The overlap, lightened rather than outlined — an outline needs a
          background colour and this mark sits on two themes. */}
      <path d="M56 25q3 0 5 2L45 56l-7-4 13-21q3-6 5-6Z"
            fill="#fff" opacity=".2" />
    </svg>
  );
}

/** Mark plus wordmark. The "ai" carries the gradient; "amaz" takes the ink
 *  colour, so the wordmark inverts correctly between themes. */
export default function Logo({ size = 26, showText = true, title = 'AmazAI' }) {
  const uid = `amazai-w-${(counter += 1)}`;
  return (
    <span className="logo" title={title}>
      <LogoMark size={size} title={showText ? undefined : title} />
      {showText && (
        <svg className="logo-word" height={size * 0.62} viewBox="0 0 132 34"
             role="img" aria-label="AmazAI">
          <defs>
            <linearGradient id={uid} x1="0" y1="1" x2="1" y2="0">
              <stop offset="0%" stopColor="#2B6BFF" />
              <stop offset="100%" stopColor="#C026D3" />
            </linearGradient>
          </defs>
          <text x="0" y="26" className="logo-amaz"
                style={{ font: '700 28px/1 var(--sans)', letterSpacing: '-1.2px' }}>
            amaz
          </text>
          <text x="66" y="26" fill={`url(#${uid})`}
                style={{ font: '700 28px/1 var(--sans)', letterSpacing: '-1.2px' }}>
            ai
          </text>
        </svg>
      )}
    </span>
  );
}
