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

      {/* The left limb: one continuous stroke turning at the apex. */}
      <path
        d="M40 12a13 13 0 0 1 20 0l2 4-14 24-18 32a12 12 0 0 1-21-12Z"
        fill={`url(#${uid}-a)`}
      />
      {/* The right form, overlapping so the crossbar reads as a seam. */}
      <path
        d="M52 34a12 12 0 0 1 21 0l17 30a12 12 0 0 1-10 18H52a12 12 0 0 1-10-18Z"
        fill={`url(#${uid}-b)`}
      />
      {/* Where they cross, lightened rather than outlined — an outline would
          need a background colour and this sits on two themes. */}
      <path d="M52 34a12 12 0 0 1 10-6l-14 24-8-6Z" fill="#fff" opacity=".22" />
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
