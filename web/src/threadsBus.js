/**
 * "Something about the conversation list changed."
 *
 * The inbox sits beside the conversation it opens, and reads its list once.
 * Opening a conversation marks it read, and a reply changes its preview -- both
 * happen in a different component, after the inbox has already drawn. Without a
 * signal the row keeps its unread dot for a thread you are looking at, which is
 * the one thing an unread marker must never do.
 *
 * A bare signal, not a store: the inbox re-reads from the API, which stays the
 * only authority on what is unread. Nothing here caches or derives a fact.
 */

const subscribers = new Set();

export function onThreadsChanged(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function threadsChanged() {
  subscribers.forEach((fn) => fn());
}
