import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyEvent, clearStream, resetPresence, setConnection,
  useConnection, usePresence, useSteps, useStreamingText,
} from './presence';

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

    // EXECUTING fires once, before a single delta or tool call has happened --
    // the Bot is about to reason, not act yet, so it reads as thinking.
    act(() => applyEvent({ type: 'run.state', state: 'EXECUTING', threadId: 'dm-eng', runId: 'run-1' }, ctx));
    expect(result.current.eng).toMatchObject({ state: 'thinking', runId: 'run-1' });

    act(() => applyEvent({ type: 'run.end', state: 'COMPLETED', threadId: 'dm-eng', summary: 'Shipped' }, ctx));
    expect(result.current.eng).toMatchObject({ state: 'complete', action: 'Shipped' });

    act(() => vi.advanceTimersByTime(4_000));
    expect(result.current).toEqual({});
  });

  it('toggles between thinking and working as a turn alternates between text and tool calls', () => {
    const { result } = renderHook(() => usePresence());

    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'Checking the logs…' }, ctx));
    expect(result.current.eng.state).toBe('thinking');

    act(() => applyEvent({ type: 'tool', threadId: 'dm-eng', runId: 'run-1', name: 'shell', summary: 'ran a command' }, ctx));
    expect(result.current.eng.state).toBe('working');

    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'Found it — ' }, ctx));
    expect(result.current.eng.state).toBe('thinking');
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


describe('the reply as it arrives', () => {
  beforeEach(() => resetPresence());
  afterEach(() => resetPresence());

  it('keeps the words a delta carried instead of counting it', () => {
    const { result } = renderHook(() => useStreamingText('dm-eng'));

    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'Looking ' }, ctx));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'into it' }, ctx));

    expect(result.current).toMatchObject({ runId: 'run-1', text: 'Looking into it' });
  });

  it('still reports the agent as writing', () => {
    const { result } = renderHook(() => usePresence());
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'hi' }, ctx));
    expect(result.current.eng).toMatchObject({ state: 'thinking', action: 'Writing a reply' });
  });

  it('starts over when a new run begins rather than appending to the last reply', () => {
    const { result } = renderHook(() => useStreamingText('dm-eng'));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'first' }, ctx));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-2', text: 'second' }, ctx));
    expect(result.current).toMatchObject({ runId: 'run-2', text: 'second' });
  });

  it('keeps two threads apart', () => {
    const two = {
      agentsForThread: (id) => (id === 'dm-a' ? ['a'] : id === 'dm-b' ? ['b'] : []),
      nameOf: (id) => id,
    };
    const a = renderHook(() => useStreamingText('dm-a'));
    const b = renderHook(() => useStreamingText('dm-b'));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-a', runId: 'r1', text: 'for a' }, two));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-b', runId: 'r2', text: 'for b' }, two));
    expect(a.result.current.text).toBe('for a');
    expect(b.result.current.text).toBe('for b');
  });

  it('accumulates nothing for a room, where words cannot be attributed', () => {
    // `delta` carries no agentId, and a room starts every member at once.
    const { result } = renderHook(() => useStreamingText('room-1'));
    act(() => applyEvent({ type: 'delta', threadId: 'room-1', runId: 'r1', text: 'whose words?' }, ctx));
    expect(result.current).toBeUndefined();
  });

  it('is cleared by the screen once the stored copy is on show', () => {
    const { result } = renderHook(() => useStreamingText('dm-eng'));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'done' }, ctx));
    act(() => clearStream('dm-eng'));
    expect(result.current).toBeUndefined();
  });

  it('survives the run ending, so the reply does not blank before it reloads', () => {
    // The server stores the message and *then* ends the run. Clearing on the
    // event would empty the bubble for the length of the reload.
    const { result } = renderHook(() => useStreamingText('dm-eng'));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'all done' }, ctx));
    act(() => applyEvent({ type: 'run.end', state: 'COMPLETED', threadId: 'dm-eng', runId: 'run-1' }, ctx));
    expect(result.current?.text).toBe('all done');
  });

  it('ignores an empty delta', () => {
    const { result } = renderHook(() => useStreamingText('dm-eng'));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: '' }, ctx));
    expect(result.current).toBeUndefined();
  });

  it('is emptied by a reset, like the rest of the store', () => {
    const { result } = renderHook(() => useStreamingText('dm-eng'));
    act(() => applyEvent({ type: 'delta', threadId: 'dm-eng', runId: 'run-1', text: 'x' }, ctx));
    act(() => resetPresence());
    expect(result.current).toBeUndefined();
  });
});

describe('whether the console is hearing anything', () => {
  beforeEach(() => resetPresence());

  it('starts out connecting, so nothing is claimed before the socket answers', () => {
    const { result } = renderHook(() => useConnection());
    expect(result.current.status).toBe('connecting');
  });

  it('reports what the socket said', () => {
    const { result } = renderHook(() => useConnection());
    act(() => setConnection('connected'));
    expect(result.current.status).toBe('connected');
    act(() => setConnection('reconnecting in 4s'));
    expect(result.current.status).toBe('reconnecting in 4s');
  });

  it('does not notify on a repeat of the same status', () => {
    let renders = 0;
    renderHook(() => { renders += 1; return useConnection(); });
    act(() => setConnection('connected'));
    const after = renders;
    act(() => setConnection('connected'));
    expect(renders).toBe(after);
  });
});
