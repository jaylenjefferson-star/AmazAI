import { useLayoutEffect, useRef } from 'react';

/**
 * Let a list's rows slide to their new place instead of jumping there.
 *
 * When an agent replies its row moves to the top. Correct, but a row that teleports
 * under your finger reads as the list flipping around. FLIP: note where each row was,
 * let React put it where it now is, then animate the difference back to nothing. Each
 * child needs `data-key`. `offsetTop` rather than a viewport rect, so scrolling the list
 * between renders is never mistaken for a move. Reduced motion gets the plain jump.
 */
export function useFlipList(listRef, order) {
  const tops = useRef(new Map());
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const next = new Map();
    for (const el of list.children) next.set(el.dataset.key, el.offsetTop);
    if (!reduce) {
      for (const el of list.children) {
        const was = tops.current.get(el.dataset.key);
        const now = next.get(el.dataset.key);
        if (was !== undefined && was !== now && typeof el.animate === 'function') {
          el.animate([{ transform: `translateY(${was - now}px)` }, { transform: 'translateY(0)' }],
                     { duration: 260, easing: 'cubic-bezier(.2,.8,.2,1)' });
        }
      }
    }
    tops.current = next;
  }, [listRef, order]);
}
