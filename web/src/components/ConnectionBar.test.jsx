import { render, screen } from '@testing-library/react';
import { act } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import ConnectionBar from './ConnectionBar';
import { resetPresence, setConnection } from '../presence';

// This project has no jest-dom, so emptiness is asserted on the DOM directly
// rather than with `toBeEmptyDOMElement`.
const drawn = (container) => container.childElementCount > 0;

describe('ConnectionBar', () => {
  beforeEach(() => resetPresence());

  it('says nothing while connected', () => {
    act(() => setConnection('connected'));
    const { container } = render(<ConnectionBar />);
    expect(drawn(container)).toBe(false);
  });

  it('says nothing on the first connect, so it cannot flash on every load', () => {
    const { container } = render(<ConnectionBar />);   // status: 'connecting'
    expect(drawn(container)).toBe(false);
  });

  it('says nothing when no socket is configured', () => {
    // There is no reconnection pending in that build; promising one would be a
    // warning that never resolves.
    act(() => setConnection('unconfigured'));
    const { container } = render(<ConnectionBar />);
    expect(drawn(container)).toBe(false);
  });

  it('warns when the socket drops, and says what it means', () => {
    act(() => setConnection('disconnected'));
    render(<ConnectionBar />);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.getByText(/out of date/i)).toBeTruthy();
  });

  it('shows the retry countdown while reconnecting', () => {
    act(() => setConnection('reconnecting in 4s'));
    render(<ConnectionBar />);
    expect(screen.getByText('Retrying in 4s')).toBeTruthy();
  });

  it('is announced to a screen reader, not only drawn', () => {
    act(() => setConnection('disconnected'));
    render(<ConnectionBar />);
    expect(screen.getByRole('status').getAttribute('aria-live')).toBe('polite');
  });

  it('disappears again once the socket comes back', () => {
    act(() => setConnection('disconnected'));
    const { container } = render(<ConnectionBar />);
    expect(drawn(container)).toBe(true);
    act(() => setConnection('connected'));
    expect(drawn(container)).toBe(false);
  });
});
