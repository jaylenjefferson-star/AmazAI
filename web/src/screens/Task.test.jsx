import { describe, expect, it } from 'vitest';
import { transcriptFor } from './Task';

describe('conversation transcript export', () => {
  it('keeps the creator-attributed first briefing before the Bot response', () => {
    const text = transcriptFor({ name: 'Janeisha Carter' }, [
      {
        type: 'agentnote', note: {
          kind: 'briefing', fromAgentId: 'chief', fromName: 'Chief',
          toAgentId: 'janeisha-carter', summary: 'Review your role and return five questions.',
        },
      },
      { type: 'message', role: 'assistant', author: 'Janeisha Carter', text: 'Here they are.' },
    ]);

    expect(text).toContain('**Chief → Janeisha Carter:** Review your role and return five questions.');
    expect(text.indexOf('Chief →')).toBeLessThan(text.indexOf('Here they are.'));
  });
});
