import { describe, expect, it, vi } from 'vitest';
import { mergeCoordination, reconcileOptimistic, threadToItems } from './threadItems';

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
      { type: 'event', text: 'Routine started', icon: 'clock', key: 'row0:event' },
      {
        type: 'steps',
        key: 'MSG#2023-11-14T22:13:20.000Z#abc:steps',
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
    expect(out[1]).toEqual({ type: 'agentnote', key: 'coord:message:2026-09-20T10:01:00Z:eng>ops', note });
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

describe('stable keys', () => {
  it('gives every stored row a key that does not depend on its position', () => {
    const rows = [
      { sk: 'MSG#2026-01-01T10:00:00Z#a', role: 'user', text: 'hi' },
      { sk: 'MSG#2026-01-01T10:00:05Z#b', kind: 'event', text: 'Woke Eng' },
      { sk: 'MSG#2026-01-01T10:00:09Z#c', role: 'assistant', text: 'hello', steps: [{ name: 'x', at: '2026-01-01T10:00:06Z' }] },
    ];
    const keys = threadToItems(rows).map((i) => i.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(threadToItems(rows.slice(1)).map((i) => i.key)).toEqual(keys.slice(1));   // dropping the first changes nothing else
  });

  it('keeps a coordination row keyed the same when rows are inserted before it', () => {
    const c = { kind: 'handoff', at: '2026-01-01T10:05:00Z', fromAgentId: 'a', toAgentId: 'b', status: 'accepted', summary: 'x' };
    const late = { type: 'message', role: 'user', text: 'z', at: '2026-01-01T10:10:00Z', key: 'm2' };
    const before = mergeCoordination([late], [c]).find((i) => i.type === 'handoff').key;
    const after = mergeCoordination([{ type: 'message', role: 'user', text: 'y', at: '2026-01-01T10:00:00Z', key: 'm1' }, late], [c])
      .find((i) => i.type === 'handoff').key;
    expect(after).toBe(before);
  });
});

describe('reconcileOptimistic', () => {
  const local = { type: 'message', role: 'user', text: 'Hi team', key: 'local:1:0' };
  const server = (sk, text) => ({ type: 'message', role: 'user', text, key: sk });

  it('hands the temporary key to the server copy so the bubble is not redrawn', () => {
    const out = reconcileOptimistic([local], [server('MSG#1', 'Hi team')]);
    expect(out[0].key).toBe('local:1:0');
  });

  it('leaves a different message alone', () => {
    expect(reconcileOptimistic([local], [server('MSG#1', 'something else')])[0].key).toBe('MSG#1');
  });

  it('claims each local message once, so two identical sends stay two', () => {
    const two = [local, { ...local, key: 'local:2:1' }];
    const out = reconcileOptimistic(two, [server('MSG#1', 'Hi team'), server('MSG#2', 'Hi team')]);
    expect(out.map((i) => i.key)).toEqual(['local:1:0', 'local:2:1']);
  });

  it('does nothing when nothing is pending', () => {
    const next = [server('MSG#1', 'Hi team')];
    expect(reconcileOptimistic([server('MSG#1', 'Hi team')], next)).toBe(next);
  });
});


describe('which run wrote a message', () => {
  it('is carried through, so a streamed reply knows when its stored copy landed', () => {
    const items = threadToItems([
      { sk: 'MSG#2026-01-01T00:00:00Z#a', role: 'assistant', text: 'Done.', runId: 'run-7' },
    ]);
    expect(items[0]).toMatchObject({ type: 'message', runId: 'run-7' });
  });

  it('is simply absent on a row that has none', () => {
    const items = threadToItems([
      { sk: 'MSG#2026-01-01T00:00:00Z#a', role: 'user', text: 'Hello' },
    ]);
    expect(items[0].runId).toBeUndefined();
  });
});
