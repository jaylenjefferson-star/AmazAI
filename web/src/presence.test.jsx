import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyEvent, resetPresence, usePresence, useSteps } from './presence';

const ctx = {
  agentsForThread: (threadId) => (threadId === 'dm-eng' ? ['eng'] : []),
  nameOf: (agentId) => agentId,
};

describe('presence events', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetPresence();
  });

  afterEach(() => {
    resetPresence();
    vi.useRealTimers();
  });

  it('turns live run events into a visible lifecycle and fades completed work', () => {
    const { result } = renderHook(() => usePresence());

    act(() => applyEvent({ type: 'run.state', state: 'EXECUTING', threadId: 'dm-eng', runId: 'run-1' }, ctx));
    expect(result.current.eng).toMatchObject({ state: 'working', runId: 'run-1' });

    act(() => applyEvent({ type: 'run.end', state: 'COMPLETED', threadId: 'dm-eng', summary: 'Shipped' }, ctx));
    expect(result.current.eng).toMatchObject({ state: 'complete', action: 'Shipped' });

    act(() => vi.advanceTimersByTime(4_000));
    expect(result.current).toEqual({});
  });

  it('records the live tool trail and closes it on an approval pause', () => {
    const { result } = renderHook(() => useSteps('dm-eng'));

    act(() => applyEvent({ type: 'tool', threadId: 'dm-eng', runId: 'run-1', name: 'search', summary: 'Looking up docs' }, ctx));
    expect(result.current).toMatchObject({ runId: 'run-1', endedAt: null, items: [{ name: 'search' }] });

    act(() => applyEvent({
      type: 'approval.requested', threadId: 'dm-eng', runId: 'run-1',
      approval: { requestedBy: { agentId: 'eng' }, action: 'send' },
    }, ctx));
    expect(result.current.endedAt).toBeTypeOf('number');
  });
});
