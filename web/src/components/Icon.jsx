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
  check: <path d="M5.75 12.75l4.25 4.25 8.25-9.5" />,
  file: <path d="M7 3.75h6.5l4 4v12.5H7V3.75ZM13.25 3.75v4.5h4.25M9.75 13h4.5M9.75 16.25h4.5" />,
  pin: <path d="M9.25 4.75h5.5l-.9 5.4 2.9 2.85H7.25l2.9-2.85-.9-5.4ZM12 13v6.25" />,
  x: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  share: <path d="M12 15.5V4.5M8 8.25l4-3.75 4 3.75M5.5 12.5v6.25h13V12.5" />,
  download: <path d="M12 4.5v11M8 11.75l4 4 4-4M5.5 19.25h13" />,
  arrowUp: <path d="M12 18.5V5.5M6.5 11l5.5-5.5 5.5 5.5" />,
  stop: <rect x="7.25" y="7.25" width="9.5" height="9.5" rx="2" />,
  mic: (
    <>
      <rect x="9" y="3.75" width="6" height="10.5" rx="3" />
      <path d="M5.75 11.5a6.25 6.25 0 0012.5 0M12 17.75v2.5" />
    </>
  ),
  paperclip: <path d="M18.5 11.75l-6.25 6.25a4 4 0 01-5.65-5.65l7-7a2.75 2.75 0 013.9 3.9l-7 7a1.4 1.4 0 01-2-2l6-6" />,
  store: (
    <>
      <path d="M4.5 9.5l1.5-5h12l1.5 5v9.75h-15V9.5Z" />
      <path d="M4.5 9.5c0 1.4 1.1 2.25 2.5 2.25S9.5 10.9 9.5 9.5c0 1.4 1.1 2.25 2.5 2.25s2.5-.85 2.5-2.25c0 1.4 1.1 2.25 2.5 2.25s2.5-.85 2.5-2.25" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0111 0M16 6.5a3 3 0 010 5.5M18.5 19a5.5 5.5 0 00-2.75-4.75" />
    </>
  ),
  play: <path d="M8.5 6.25v11.5l9-5.75-9-5.75Z" />,
  edit: <path d="M5 19l1-4 9.5-9.5a1.9 1.9 0 012.7 2.7L8.7 17.7 5 19ZM14 7l3 3" />,
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
