import { describe, expect, it } from 'vitest';
import { previewOf } from './Inbox';

describe('conversation preview attribution', () => {
  it('labels only the operator’s own message as You', () => {
    expect(previewOf({ previewRole: 'user', preview: 'Start this.' }))
      .toBe('You: Start this.');
  });

  it('names the Bot who briefed a newly created teammate', () => {
    expect(previewOf({
      previewRole: 'briefing', previewAuthor: 'Chief',
      preview: 'Review your role and return five questions.',
    })).toBe('Chief: Review your role and return five questions.');
  });

  it('keeps a normal Bot reply unprefixed because the row already names it', () => {
    expect(previewOf({ previewRole: 'assistant', preview: 'Here are the questions.' }))
      .toBe('Here are the questions.');
  });
});
