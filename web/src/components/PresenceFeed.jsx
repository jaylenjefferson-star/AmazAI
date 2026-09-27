import { useEffect } from 'react';
import { api } from '../api';
import { applyEvent, resetPresence, seed, setConnection } from '../presence';
import { connect } from '../ws';

/**
 * Feeds the presence store from the console's one socket.
 *
 * Renders nothing. It is mounted once, in the shell, so every screen that draws
 * a companion sees the same state without each opening a connection -- the
 * previous socket client lived in a screen that was never mounted, which left
 * the whole console polling per run and no live state anywhere.
 *
 * Failure is quiet but no longer silent. A socket that cannot connect still
 * blocks nothing and still lets the inbox fall back to what the API says, but
 * its status now reaches the store, so `ConnectionBar` can say the console is
 * not hearing anything instead of leaving a dead socket looking like an idle
 * Bot.
 */
export default function PresenceFeed() {
  useEffect(() => {
    const threads = new Map();   // threadId -> { agentIds, kind }
    const names = new Map();     // agentId -> display name

    Promise.allSettled([api.threads(), api.agents(), api.presence()]).then(([t, a, p]) => {
      if (t.status === 'fulfilled') {
        for (const th of t.value?.threads || []) threads.set(th.threadId, th);
      }
      if (a.status === 'fulfilled') {
        for (const ag of a.value?.agents || []) names.set(ag.agentId, ag.name);
      }
      // Seed the store from durable state so a reload reflects what the org is
      // doing rather than an empty cache; the socket below then keeps it live.
      // Quiet on failure, like the socket itself -- a missed seed is a stale
      // animation until the next event, never a wrong answer.
      if (p.status === 'fulfilled') seed(p.value);
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

    const socket = connect((ev) => applyEvent(ev, ctx), setConnection);
    return () => { socket.close(); resetPresence(); };
  }, []);

  return null;
}
