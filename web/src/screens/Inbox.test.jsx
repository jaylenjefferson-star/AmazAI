import { describe, expect, it } from 'vitest';
import { attentionOf, previewOf } from './Inbox';

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

describe('inbox surfaces only meaningful items', () => {
  it('classifies a pending approval as an approval', () => {
    expect(attentionOf({ state: 'approval', action: 'Waiting for your approval' })).toBe('approval');
  });

  it('tells a needs-answer apart from an approval by the action line', () => {
    // Both light the `approval` bucket over the socket; the action line the
    // backend already wrote is what distinguishes an answer from a decision.
    expect(attentionOf({ state: 'approval', action: 'Waiting for your answer' })).toBe('answer');
    expect(attentionOf({ state: 'approval', action: 'Waiting for you to sign in' })).toBe('answer');
  });

  it('classifies a hard failure and finished work as distinct items', () => {
    expect(attentionOf({ state: 'blocked' })).toBe('failed');
    expect(attentionOf({ state: 'complete' })).toBe('done');
  });

  it('treats ordinary activity as no item at all', () => {
    // A Bot merely thinking, working, waiting or idle is activity, not
    // something that needs the operator -- so it is never an inbox item.
    for (const state of ['thinking', 'working', 'waiting', 'idle']) {
      expect(attentionOf({ state, action: 'Typing' })).toBeNull();
    }
  });
});
