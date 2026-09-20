import { useEffect } from 'react';
import { api } from '../api';
import { applyEvent, resetPresence } from '../presence';
import { connect } from '../ws';

/**
 * Feeds the presence store from the console's one socket.
 *
 * Renders nothing. It is mounted once, in the shell, so every screen that draws
 * a companion sees the same state without each opening a connection -- the
 * previous socket client lived in a screen that was never mounted, which left
 * the whole console polling per run and no live state anywhere.
 *
 * Failure is quiet by design. A socket that cannot connect means animations
 * stay at rest and the inbox falls back to what the API says; nothing here is
 * allowed to block a screen or report an error about a decoration.
 */
export default function PresenceFeed() {
  useEffect(() => {
    const threads = new Map();   // threadId -> { agentIds, kind }
    const names = new Map();     // agentId -> display name

    Promise.allSettled([api.threads(), api.agents()]).then(([t, a]) => {
      if (t.status === 'fulfilled') {
        for (const th of t.value?.threads || []) threads.set(th.threadId, th);
      }
      if (a.status === 'fulfilled') {
        for (const ag of a.value?.agents || []) names.set(ag.agentId, ag.name);
      }
    });

    const ctx = {
      agentsForThread(threadId) {
        if (!threadId) return [];
        if (threadId.startsWith('dm-')) return [threadId.slice(3)];
        const th = threads.get(threadId);
        return th && th.kind !== 'room' && th.agentIds?.length === 1 ? th.agentIds : [];
      },
      nameOf: (id) => names.get(id) || id || 'another Bot',
    };

    const socket = connect((ev) => applyEvent(ev, ctx));
    return () => { socket.close(); resetPresence(); };
  }, []);

  return null;
}
