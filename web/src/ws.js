import { idToken } from './auth';
import { DEMO, demoConnect } from './demo';

const URL = import.meta.env.VITE_WS_URL;

/**
 * One socket for the whole console, with exponential backoff reconnect.
 * Routines firing while you are away push into this same socket, so the
 * console updates itself without polling.
 */
export function connect(onEvent, onStatus) {
  if (DEMO) return demoConnect(onEvent, onStatus);

  let socket = null;
  let attempt = 0;
  let closed = false;
  let timer = null;

  async function open() {
    if (closed) return;
    try {
      const token = await idToken();
      socket = new WebSocket(`${URL}?token=${encodeURIComponent(token)}`);
    } catch {
      return schedule();
    }

    socket.onopen = () => { attempt = 0; onStatus?.('connected'); };
    socket.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch { /* ignore malformed frames */ }
    };
    socket.onclose = () => { onStatus?.('disconnected'); schedule(); };
    socket.onerror = () => socket?.close();
  }

  function schedule() {
    if (closed) return;
    const delay = Math.min(30000, 1000 * 2 ** attempt++);
    onStatus?.(`reconnecting in ${Math.round(delay / 1000)}s`);
    clearTimeout(timer);
    timer = setTimeout(open, delay);
  }

  open();

  return {
    send: (payload) => socket?.readyState === 1 && socket.send(JSON.stringify(payload)),
    close: () => { closed = true; clearTimeout(timer); socket?.close(); },
  };
}
