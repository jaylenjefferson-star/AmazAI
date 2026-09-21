const ms = (iso) => (iso ? Date.parse(iso) : undefined);

/**
 * Stored message rows -> what the timeline draws.
 *
 * One function for a direct chat and a channel, so the two cannot disagree about
 * what a row means. A row is one of:
 *
 *   an **event**   a line of history the system wrote when something really
 *                  happened (a routine created, a fact saved, a Bot woken)
 *   a **turn**     words, and/or the trail behind them (`steps`, with Auto
 *                  Review's verdict on each) and/or proposals (`cards`)
 *
 * A turn that only paused on an approval has a trail and no words, so the trail
 * is drawn and the empty bubble is not.
 */
export function threadToItems(messages = []) {
  const out = [];
  for (const m of messages) {
    if (m.kind === 'event') {
      out.push({ type: 'event', text: m.text, icon: m.icon });
      continue;
    }
    if (m.steps?.length) {
      out.push({ type: 'steps', steps: {
        items: m.steps.map((s) => ({ ...s, at: ms(s.at) })),
        startedAt: ms(m.startedAt) ?? ms(m.steps[0].at),
        endedAt: ms(m.endedAt) ?? Date.now(),
      } });
    }
    if (m.text || m.cards?.length) {
      out.push({
        type: 'message', role: m.role, author: m.author, text: m.text,
        suggestions: m.suggestions, cards: m.cards,
        // Rows are keyed `MSG#<iso>#<rand>`; the time is in the key.
        at: m.at || String(m.sk || '').split('#')[1],
        key: m.sk,
      });
    }
  }
  return out;
}

/**
 * Put the work between agents into the conversation, in the order it happened.
 *
 * A handoff becomes a delegation object and a message between agents a quiet
 * line. Only timestamped rows are compared: an event line carries no time, so it
 * keeps its place. A coordination item with no later message to sit before goes at
 * the end, where "just happened" belongs.
 */
export function mergeCoordination(items, coordination = []) {
  const list = [...items];
  for (const c of coordination) {
    const node = c.kind === 'handoff'
      ? { type: 'handoff', handoff: { fromAgentId: c.fromAgentId, toAgentId: c.toAgentId, status: c.status, goal: c.summary, constraints: [] } }
      : { type: 'agentnote', note: c };
    const at = list.findIndex((x) => x.type === 'message' && x.at && c.at && x.at > c.at);
    if (at === -1) list.push(node); else list.splice(at, 0, node);
  }
  return list;
}
