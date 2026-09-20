import { useEffect, useState } from 'react';

/**
 * A viewport breakpoint as state, not a one-time read.
 *
 * `Topbar.jsx` and `theme.js` each read `matchMedia` once, for a value that
 * only matters at first paint. A layout decision is not that -- resizing a
 * window across 900px has to move a companion between "a route" and "a pane
 * beside the list" without a reload, or the boundary would only ever be
 * true for whichever width the tab happened to open at.
 */
export function useMediaQuery(query) {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && Boolean(window.matchMedia?.(query).matches),
  );

  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return undefined;
    const onChange = () => setMatches(mq.matches);
    onChange();
    // Safari < 14 only has the deprecated pair; both are kept rather than
    // picked by sniffing a version.
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else mq.addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', onChange);
      else mq.removeListener(onChange);
    };
  }, [query]);

  return matches;
}
