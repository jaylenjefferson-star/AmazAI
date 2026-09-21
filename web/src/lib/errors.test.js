import { describe, expect, it } from 'vitest';
import { COPY, friendly, isTransportError, retryLabel } from './errors';

describe('friendly', () => {
  it.each(['Load failed', 'Failed to fetch', 'NetworkError when attempting to fetch resource.',
           'The request timed out', 'Network request failed'])(
    'turns the transport failure %j into one calm sentence', (raw) => {
      expect(friendly(new Error(raw))).toBe('Having trouble connecting.');
      expect(isTransportError(new Error(raw))).toBe(true);
      expect(retryLabel(new Error(raw))).toBe('Try again');
    });

  it('never puts a raw technical message in front of a person', () => {
    for (const raw of ['500 Internal Server Error', 'TypeError: x is undefined', '{"error":"boom"}',
                       'Traceback (most recent call last)', 'x'.repeat(200)]) {
      expect(friendly(new Error(raw), "Couldn't load this.")).toBe("Couldn't load this.");
    }
  });

  it('passes a server sentence through when it reads like one', () => {
    expect(friendly(new Error('gmail is not connected yet. Finish signing in on the connect page, then try again.')))
      .toMatch(/not connected yet/);
  });

  it('says what to do when the session ended', () => {
    expect(friendly(new Error('unauthorized'))).toBe('Your session ended. Sign in again.');
  });

  it('names the agent when creation fails, and says nothing was lost', () => {
    expect(COPY.createAgent('Tanzie')).toBe("Couldn't create Tanzie. Nothing was lost. Try again.");
  });
});
