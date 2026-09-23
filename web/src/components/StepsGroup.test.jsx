import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StepsGroup from './StepsGroup';

const steps = (over = {}) => ({
  startedAt: 1_000, endedAt: 15_000,
  items: [
    { name: 'shell', summary: 'git status → clean', review: { decision: 'allowed', rule: 'sandbox', reason: 'ran in its own sandbox' } },
    { name: 'GMAIL_SEND_EMAIL', summary: 'to a@b.co', review: { decision: 'asked', rule: 'default', reason: 'a write' } },
  ],
  ...over,
});

describe('StepsGroup', () => {
  it('stays collapsed while the run is live, not pushed open on you', () => {
    render(<StepsGroup steps={steps({ endedAt: undefined })} />);
    expect(screen.getByRole('button', { name: /Working/ })).toBeTruthy();
    expect(screen.queryByText('Terminal')).toBeNull();
  });

  it('shows a plain label for each tool and keeps the exact name on hover, once opened', () => {
    render(<StepsGroup steps={steps({ endedAt: undefined })} />);
    fireEvent.click(screen.getByRole('button', { name: /Working/ }));
    const terminal = screen.getByText('Terminal');
    expect(terminal.getAttribute('title')).toBe('shell');
    expect(screen.getByText('Gmail: send email').getAttribute('title')).toBe('GMAIL_SEND_EMAIL');
    // The raw identifier is not what is printed on the line.
    expect(screen.queryByText('shell')).toBeNull();
  });

  it('shows what was actually done, untouched, once opened', () => {
    render(<StepsGroup steps={steps({ endedAt: undefined })} />);
    fireEvent.click(screen.getByRole('button', { name: /Working/ }));
    expect(screen.getByText('git status → clean')).toBeTruthy();
    expect(screen.getByText('to a@b.co')).toBeTruthy();
  });

  it('folds to one line when the run has ended and says what needed you', () => {
    render(<StepsGroup steps={steps()} />);
    expect(screen.getByRole('button', { name: /Worked for 14s · 2 steps · 1 asked/ })).toBeTruthy();
    expect(screen.queryByText('Terminal')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Worked for/ }));
    expect(screen.getByText('Terminal')).toBeTruthy();
  });
});
