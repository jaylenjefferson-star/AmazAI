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
  back: <path d="M14.5 5.5L8 12l6.5 6.5" />,
  forward: <path d="M9.5 5.5L16 12l-6.5 6.5" />,
  arrowright: <path d="M4.5 12h14M13.5 6.75L18.75 12l-5.25 5.25" />,
  send: <path d="M12 19V5.5M6.5 11L12 5.5 17.5 11" />,
  waveform: <path d="M5 10v4M8.5 7v10M12 4.5v15M15.5 8v8M19 10.5v3" />,
  computer: (
    <>
      <rect x="3.5" y="4.5" width="17" height="11.5" rx="2.25" />
      <path d="M8.5 20h7M12 16v4" />
    </>
  ),
  image: (
    <>
      <rect x="3.75" y="4.75" width="16.5" height="14.5" rx="2.5" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M4.5 17l4.75-4.5 3.5 3.25 3-2.5 4.25 3.75" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8.25h3l1.5-2.25h7L17 8.25h3v10.25H4V8.25Z" />
      <circle cx="12" cy="13.25" r="3.25" />
    </>
  ),
  upload: <path d="M12 16V5.5M7.5 10L12 5.5l4.5 4.5M5 19h14" />,
  bot: (
    <>
      <rect x="4.5" y="8" width="15" height="11" rx="3.25" />
      <path d="M12 4.75V8" />
      <circle cx="9.25" cy="13" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="14.75" cy="13" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  hash: <path d="M9.5 4.5L8 19.5M16 4.5l-1.5 15M5 9.25h14.5M4.5 14.75H19" />,
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3.75v2.5M12 17.75v2.5M3.75 12h2.5M17.75 12h2.5M6.2 6.2L8 8M16 16l1.8 1.8M17.8 6.2L16 8M8 16l-1.8 1.8" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.75" r="3.5" />
      <path d="M5 19.5c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" />
    </>
  ),
  logout: <path d="M14 4.75H6.75v14.5H14M10.5 12h9M16.5 8.75L19.75 12l-3.25 3.25" />,
  palette: (
    <>
      <path d="M12 4a8 8 0 100 16c1.2 0 1.75-.75 1.75-1.5 0-1.5-1.25-1.5-1.25-2.75 0-.9.75-1.5 1.75-1.5H16A4 4 0 0020 10c0-3.3-3.6-6-8-6Z" />
      <circle cx="8.25" cy="11" r=".9" fill="currentColor" stroke="none" />
      <circle cx="12" cy="8" r=".9" fill="currentColor" stroke="none" />
      <circle cx="15.5" cy="10" r=".9" fill="currentColor" stroke="none" />
    </>
  ),
  card: (
    <>
      <rect x="3.5" y="5.75" width="17" height="12.5" rx="2.5" />
      <path d="M3.5 10h17M7 14.75h3.5" />
    </>
  ),
  building: <path d="M5.5 20V5.5A1.5 1.5 0 017 4h6a1.5 1.5 0 011.5 1.5V20M14.5 9.5H17a1.5 1.5 0 011.5 1.5v9M3.5 20h17M8.5 8.5h3M8.5 12h3M8.5 15.5h3" />,
  spark: (
    <>
      <path d="M12 3.5l1.9 5.6 5.6 1.9-5.6 1.9L12 18.5l-1.9-5.6L4.5 11l5.6-1.9L12 3.5Z" />
      <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.75l7 2.5v5.5c0 4.2-2.9 7.3-7 8.5-4.1-1.2-7-4.3-7-8.5v-5.5l7-2.5Z" />
      <path d="M9 12l2.25 2.25L15.5 9.75" />
    </>
  ),
  bolt: <path d="M13 3.75L6.5 13H12l-1 7.25L17.5 11H12l1-7.25Z" />,
  task: (
    <>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M8.25 12.25l2.6 2.6 4.9-5.6" />
    </>
  ),
  history: <path d="M3.75 12a8.25 8.25 0 102.5-5.9M3.75 4.5v4.25h4.25M12 7.75V12l3 1.75" />,
  trash: <path d="M5 7h14M9.5 7V4.75h5V7M7 7l.75 12.25h8.5L17 7M10 10.75v5M14 10.75v5" />,
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
