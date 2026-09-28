import { describe, expect, it } from 'vitest';
import { coordinationSummary, memberLine } from './RoomCoordination';

describe('room member line', () => {
  it('prefers the durable action line the backend derived', () => {
    expect(memberLine({ state: 'thinking', action: 'Planning' })).toBe('Planning');
  });

  it('falls back to the canonical verb when there is no action', () => {
    // The verb is the coarse fallback, kept word-for-word with Companion.STATES.
    expect(memberLine({ state: 'working', action: '' })).toBe('working on it');
    expect(memberLine({ state: 'waiting', action: '' })).toBe('waiting on a teammate');
  });

  it('maps the backend needs_approval bucket onto the approval verb', () => {
    expect(memberLine({ state: 'needs_approval', action: '' })).toBe('waiting for your approval');
  });
});

describe('room coordination summary', () => {
  const nameOf = (id) => ({ eng: 'Engineering', chief: 'Chief' }[id] || id);

  it('is empty when there is nothing to say', () => {
    expect(coordinationSummary(null, nameOf)).toBe('');
    expect(coordinationSummary({ members: [] }, nameOf)).toBe('');
  });

  it('leads with a decision the operator owes', () => {
    expect(coordinationSummary({ needsApproval: true, stageOwnerAgentId: 'eng' }, nameOf))
      .toBe('Waiting for your approval');
  });

  it('names the stage owner when someone is on it', () => {
    expect(coordinationSummary({ stageOwnerAgentId: 'eng' }, nameOf)).toBe('Engineering is working');
  });

  it('says waiting when the room is parked on a teammate', () => {
    expect(coordinationSummary({ waiting: true }, nameOf)).toBe('Waiting on a teammate');
  });

  it('surfaces a delivered artifact when nothing is actively moving', () => {
    expect(coordinationSummary({ artifactProduced: true }, nameOf)).toBe('Delivered an artifact');
  });
});
