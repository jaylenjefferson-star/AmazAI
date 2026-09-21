/**
 * What a person is told when something goes wrong.
 *
 * A raw failure ("Load failed", "Failed to fetch", "500 Internal Server Error",
 * a stack-shaped message) says nothing a person can act on and makes the whole
 * product feel unfinished. Everything user-facing goes through here, so there is
 * one place that decides the words, and a transport failure is always the same
 * calm sentence.
 */

const TRANSPORT = /load failed|failed to fetch|networkerror|network request failed|network error|timed? ?out|aborted|offline|err_/i;
const SESSION = /unauthori[sz]ed|\b401\b|token.*(expired|invalid)|not signed in/i;
const DENIED = /forbidden|\b403\b|not allowed|denied/i;
// Messages we would never put in front of someone: codes, stack-shaped text,
// JSON, class names, or anything long enough to be a dump.
const TECHNICAL = /^\d{3}\b|internal server error|typeerror|referenceerror|syntaxerror|undefined|\bnull\b|\{.*\}|<\/?[a-z]|traceback|exception|at \S+:\d+|ECONN|ENOTFOUND/i;

export const isTransportError = (err) => TRANSPORT.test(String(err?.message ?? err ?? ''));

/**
 * A sentence for the person, never a raw failure.
 *
 * `fallback` is what to say when the cause is not one they can act on. A
 * server-authored sentence that reads like a sentence (a 409 that says an app
 * is not connected yet) is passed through, because it is exactly what they
 * need; anything technical is not.
 */
export function friendly(err, fallback = "Couldn't load this.") {
  const raw = String(err?.message ?? err ?? '').trim();
  if (!raw || TRANSPORT.test(raw)) return 'Having trouble connecting.';
  if (SESSION.test(raw)) return 'Your session ended. Sign in again.';
  if (DENIED.test(raw) && raw.length < 80) return "You don't have access to that.";
  if (TECHNICAL.test(raw) || raw.length > 140) return fallback;
  return raw;
}

/** Whether "Try again" is the right label (a connection problem) or "Retry". */
export const retryLabel = (err) => (isTransportError(err) ? 'Try again' : 'Retry');

export const COPY = {
  load: "Couldn't load this.",
  loadList: "Couldn't load your conversations.",
  connecting: 'Having trouble connecting.',
  createAgent: (name) => `Couldn't create ${name || 'your agent'}. Nothing was lost. Try again.`,
  createRoom: "Couldn't create the room. Nothing was lost. Try again.",
  send: "Couldn't send that. It's still in the box.",
};
