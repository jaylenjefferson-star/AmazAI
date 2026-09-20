import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Pinned conversations.
 *
 * An account fact, kept in settings on the server, so a pin made on a laptop is
 * on the phone -- the same reason first-run state stopped living in
 * localStorage. One module-level copy with listeners, so the inbox strip and
 * the "Pin to top" row in a conversation's menu can never disagree about what
 * is pinned; two components each holding their own list would.
 */

// Mirrors `settings.MAX_PINNED`. The API is the authority; this only lets the
// console say so before the round trip rather than after.
export const MAX_PINNED = 12;

let pinned = null;               // null until the first read
const listeners = new Set();

function set(next) {
  pinned = next;
  listeners.forEach((fn) => fn(next));
}

export function usePins() {
  const [pins, setPins] = useState(pinned ?? []);

  useEffect(() => {
    listeners.add(setPins);
    if (pinned === null) {
      // A failed read is an empty strip, not an error: pins are a convenience.
      api.settings().then((s) => set(s.pinned || [])).catch(() => set([]));
    } else {
      setPins(pinned);
    }
    return () => listeners.delete(setPins);
  }, []);

  const toggle = useCallback(async (threadId) => {
    const before = pinned ?? [];
    const on = before.includes(threadId);
    if (!on && before.length >= MAX_PINNED) {
      throw new Error(`You can pin up to ${MAX_PINNED} conversations. Unpin one first.`);
    }
    const next = on ? before.filter((id) => id !== threadId) : [...before, threadId];
    // Optimistic: the strip should move when tapped, not after a round trip.
    set(next);
    try {
      await api.saveSettings({ pinned: next });
    } catch (err) {
      set(before);
      throw err;
    }
  }, []);

  return { pins, toggle };
}
