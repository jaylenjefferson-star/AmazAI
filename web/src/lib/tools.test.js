import { describe, expect, it } from 'vitest';
import { BUILT_IN, stepLabel } from './tools';

describe('stepLabel', () => {
  it('says the built-in tools in plain words', () => {
    expect(stepLabel('shell')).toBe('Terminal');
    expect(stepLabel('file_operations')).toBe('Files');
    expect(BUILT_IN.browser).toBe('Browser');
  });

  it('names what the loop does in words, not identifiers', () => {
    expect(stepLabel('request_connector')).toBe('Asked to connect an app');
    expect(stepLabel('agent.create')).toBe('Suggested a new Bot');
    expect(stepLabel('agent.created')).toBe('Created a Bot');       // it exists, not just proposed
    expect(stepLabel('agent.update')).toBe('Refined a Bot');
    expect(stepLabel('create_agent')).toBe('Created a Bot');
    expect(stepLabel('message_agent')).toBe('Messaged a teammate');
  });

  it('reads a Composio tool as the app, then what it does', () => {
    expect(stepLabel('GMAIL_SEND_EMAIL')).toBe('Gmail: send email');
    expect(stepLabel('SLACK_FETCH_CONVERSATION_HISTORY')).toBe('Slack: fetch conversation history');
  });

  it('leaves a name it does not know exactly as it is', () => {
    expect(stepLabel('pr.create')).toBe('pr.create');
    expect(stepLabel('something_new')).toBe('something_new');
    expect(stepLabel('')).toBe('');
  });
});
