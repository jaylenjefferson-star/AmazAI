import { describe, expect, it } from 'vitest';
import { formatDate } from './PolicyPage';

describe('policy dates', () => {
  it('renders a date-only effective date as the stated calendar day', () => {
    expect(formatDate('2026-09-28')).toBe('September 28, 2026');
  });

  it('preserves an unparseable source value instead of hiding it', () => {
    expect(formatDate('date pending')).toBe('date pending');
  });
});
