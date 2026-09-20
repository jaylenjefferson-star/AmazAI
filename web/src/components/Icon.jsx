/**
 * The interface icons, drawn rather than typed.
 *
 * These were Unicode glyphs — `⌕`, `◉`, `⚙`, a literal `+`. A glyph is
 * whatever the fallback font decides it is: a different weight from the text
 * beside it, a different optical size, a different baseline, and a different
 * shape on every platform. That inconsistency is most of what reads as
 * unfinished, and no amount of spacing fixes it.
 *
 * One geometry for all of them: a 24 grid, 1.75 stroke, round caps and
 * joins, `currentColor` so an icon inherits the ink of whatever it sits in
 * and needs no second colour per theme. Stroke, not fill, so the weight
 * matches text rather than blotting next to it.
 *
 * `size` is the box. At 18 and below the stroke is nudged up, because a
 * hairline that looks right at 24 disappears at 16.
 */

const PATHS = {
  search: (
    <>
      <circle cx="11" cy="11" r="6.75" />
      <path d="M16 16l4.5 4.5" />
    </>
  ),
  plus: <path d="M12 5.75v12.5M5.75 12h12.5" />,
  inbox: (
    <>
      <path d="M4.25 13.25h3.9a1 1 0 0 1 .9.56l.4.82a1 1 0 0 0 .9.56h3.3a1 1 0 0 0 .9-.56l.4-.82a1 1 0 0 1 .9-.56h3.9" />
      <path d="M6.6 4.75h10.8a1.5 1.5 0 0 1 1.42 1.02l1.93 5.8v5.68a1.75 1.75 0 0 1-1.75 1.75H5.25a1.75 1.75 0 0 1-1.75-1.75V11.57l1.93-5.8A1.5 1.5 0 0 1 6.6 4.75Z" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3.75v4.5M15 3.75v4.5" />
      <path d="M6.75 8.25h10.5v3.25a5.25 5.25 0 0 1-10.5 0Z" />
      <path d="M12 16.75v3.5" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 7.5h7M15.5 7.5h4.5M4 16.5h4.5M13 16.5h7" />
      <circle cx="13" cy="7.5" r="2.25" />
      <circle cx="10.5" cy="16.5" r="2.25" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 7.25v5.15l3.1 1.85" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3.4 3.6 7.9 12 12.4l8.4-4.5z" />
      <path d="M3.6 12 12 16.5l8.4-4.5M3.6 16.1 12 20.6l8.4-4.5" />
    </>
  ),
  chevronLeft: <path d="M14.5 6.25 8.75 12l5.75 5.75" />,
  more: (
    <>
      <circle cx="12" cy="5.25" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="18.75" r="1.4" fill="currentColor" stroke="none" />
    </>
  ),
};

export default function Icon({ name, size = 20, className = '' }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg
      className={`icon ${className}`}
      width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor"
      strokeWidth={size <= 18 ? 1.9 : 1.75}
      strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false"
    >
      {path}
    </svg>
  );
}
