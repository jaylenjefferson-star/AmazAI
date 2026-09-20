import { describe, expect, it, vi } from 'vitest';
import { threadToItems } from './threadItems';

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
