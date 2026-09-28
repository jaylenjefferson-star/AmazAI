import { useEffect, useRef, useState } from 'react';

/**
 * Reveal-on-enter, the honest way: a single IntersectionObserver flips one
 * boolean when the element first scrolls into view. It is used only to
 * *stagger* CSS that would otherwise play on load — never to block reading.
 * Anyone with prefers-reduced-motion still sees everything (the CSS shows the
 * final state); the observer just decides when the entrance plays.
 *
 * Falls back to "shown" immediately if IntersectionObserver is absent (e.g.
 * jsdom in tests), so nothing is ever hidden without motion.
 */
export default function useReveal(options = { threshold: 0.25 }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setShown(true);
      return undefined;
    }
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          setShown(true);
          obs.disconnect();
        }
      });
    }, options);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return [ref, shown];
}
