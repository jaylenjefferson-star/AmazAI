import { describe, expect, it } from 'vitest';
import { BUILT_IN, VERDICT, stepLabel } from './tools';

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

  it('labels the collaboration steps that used to leak their identifier', () => {
    // These two had no entry, so a run that looked up a teammate and opened a
    // room drew "agent.find" and "group_chat.create" mid-sentence.
    expect(stepLabel('agent.find')).toBe('Looked up teammates');
    expect(stepLabel('find_agents')).toBe('Looked up teammates');
    expect(stepLabel('group_chat.create')).toBe('Started a group chat');
    expect(stepLabel('create_group_chat')).toBe('Started a group chat');
    expect(stepLabel('routine.create')).toBe('Suggested a routine');
  });

  it('labels create_artifact/read_artifact instead of leaking them raw', () => {
    // Same failure mode as agent.find above: these had no entry either, so a
    // run that saved or opened a file drew the raw tool name mid-sentence.
    expect(stepLabel('create_artifact')).toBe('Created a file');
    expect(stepLabel('read_artifact')).toBe('Opened a file');
  });

  it('reads every label as something already done', () => {
    const past = /^(Terminal|Files|Browser|Code)$|^(Looked|Used|Asked|Suggested|Created|Refined|Saved|Messaged|Handed|Started|Opened)\b/;
    for (const name of ['shell', 'connector_search', 'connector_call', 'request_connector',
                        'request_approval', 'propose_routine', 'routine.create', 'create_agent',
                        'update_agent', 'propose_agent', 'agent.create', 'agent.created',
                        'agent.update', 'find_agents', 'agent.find', 'create_group_chat',
                        'group_chat.create', 'propose_skill', 'skill.create',
                        'propose_shared_memory', 'memory.publish', 'remember',
                        'message_agent', 'handoff', 'create_artifact', 'read_artifact']) {
      expect(stepLabel(name), name).toMatch(past);
    }
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

describe('VERDICT', () => {
  it('is one vocabulary, so two screens cannot name one decision differently', () => {
    // The trail said "Allowed / Denied" and a Bot's desk said "Ran / Stopped"
    // about the very same review, from two hardcoded maps.
    expect(VERDICT).toEqual({ allowed: 'Allowed', asked: 'Asked', denied: 'Denied' });
  });
});
