import { describe, expect, it, vi } from 'vitest';
import { mergeCoordination, threadToItems } from './threadItems';

describe('threadToItems', () => {
  it('keeps events separate from turns and normalizes step timestamps', () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_700_000_010_000);
    const items = threadToItems([
      { kind: 'event', text: 'Routine started', icon: 'clock' },
      {
        role: 'assistant', author: 'Engineering', text: 'Done', sk: 'MSG#2023-11-14T22:13:20.000Z#abc',
        startedAt: '2023-11-14T22:13:20.000Z',
        steps: [{ name: 'search', at: '2023-11-14T22:13:21.000Z' }],
      },
    ]);

    expect(items).toEqual([
      { type: 'event', text: 'Routine started', icon: 'clock' },
      {
        type: 'steps',
        steps: {
          items: [{ name: 'search', at: 1_700_000_001_000 }],
          startedAt: 1_700_000_000_000,
          endedAt: 1_700_000_010_000,
        },
      },
      {
        type: 'message', role: 'assistant', author: 'Engineering', text: 'Done',
        suggestions: undefined, cards: undefined, at: '2023-11-14T22:13:20.000Z',
        key: 'MSG#2023-11-14T22:13:20.000Z#abc',
      },
    ]);
    now.mockRestore();
  });

  it('draws an approval-only turn as steps without an empty message bubble', () => {
    expect(threadToItems([{ role: 'assistant', steps: [{ name: 'send' }] }])).toHaveLength(1);
    expect(threadToItems([{ role: 'assistant', cards: [{ type: 'approval' }] }])).toMatchObject([
      { type: 'message', cards: [{ type: 'approval' }] },
    ]);
  });
});

describe('mergeCoordination', () => {
  const msg = (text, at) => ({ type: 'message', role: 'user', text, at });
  const handoff = { kind: 'handoff', at: '2026-09-20T10:05:00Z', fromAgentId: 'cos', toAgentId: 'eng', status: 'accepted', summary: 'Cut the release.' };

  it('puts a handoff before the first message that came after it', () => {
    const out = mergeCoordination([msg('a', '2026-09-20T10:00:00Z'), msg('b', '2026-09-20T10:10:00Z')], [handoff]);
    expect(out.map((i) => i.type)).toEqual(['message', 'handoff', 'message']);
    expect(out[1].handoff).toMatchObject({ fromAgentId: 'cos', toAgentId: 'eng', goal: 'Cut the release.' });
  });

  it('appends work that happened after the last message', () => {
    const out = mergeCoordination([msg('a', '2026-09-20T10:00:00Z')], [handoff]);
    expect(out.map((i) => i.type)).toEqual(['message', 'handoff']);
  });

  it('renders a message between agents as a note, not a chat bubble', () => {
    const note = { kind: 'message', at: '2026-09-20T10:01:00Z', fromAgentId: 'eng', toAgentId: 'ops', summary: 'Invalidate later.' };
    const out = mergeCoordination([msg('a', '2026-09-20T10:00:00Z')], [note]);
    expect(out[1]).toEqual({ type: 'agentnote', note });
  });

  it('leaves event lines where they were and never drops or duplicates a row', () => {
    const event = { type: 'event', text: 'Routine created' };
    const items = [msg('a', '2026-09-20T10:00:00Z'), event, msg('b', '2026-09-20T10:10:00Z')];
    const out = mergeCoordination(items, [handoff]);
    expect(out).toHaveLength(4);
    expect(out.filter((i) => i === event)).toHaveLength(1);
    expect(mergeCoordination(items, [])).toEqual(items);
  });
});
