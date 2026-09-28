import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Landing renders under a Router and reads useAuth0 for its CTAs; mock auth0
// the same way Pricing.test.jsx does so startLogin resolves to a spy.
const mockLoginWithRedirect = vi.fn();
vi.mock('../auth0', () => ({
  useAuth0: () => ({ isAuthenticated: false, loginWithRedirect: mockLoginWithRedirect }),
  startLogin: (loginWithRedirect, opts) => loginWithRedirect(opts),
}));

import Landing from './Landing';

const renderIt = () => render(<MemoryRouter><Landing /></MemoryRouter>);

describe('Landing homepage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('leads with the exact hero positioning and CTAs', () => {
    renderIt();
    expect(screen.getByRole('heading', { level: 1, name: 'Your work has a new operating system.' })).toBeTruthy();
    expect(screen.getByText('No credit card required · Set up in minutes')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Start for free' }));
    expect(mockLoginWithRedirect).toHaveBeenCalledWith(
      expect.objectContaining({ signup: true, returnTo: '/welcome' }));
  });

  it('introduces each product concept with a stable anchor id', () => {
    const { container } = renderIt();
    ['companions', 'rooms', 'routines', 'artifacts'].forEach((id) => {
      expect(container.querySelector(`#${id}`)).toBeTruthy();
    });
  });

  it('gives the team use cases a keyboard-navigable tablist that swaps copy', () => {
    renderIt();
    const tablist = screen.getByRole('tablist', { name: 'Teams' });
    const founders = within(tablist).getByRole('tab', { name: 'Founders' });
    expect(founders.getAttribute('aria-selected')).toBe('true');
    // ArrowRight moves selection to the next tab (automatic activation).
    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    const marketing = within(tablist).getByRole('tab', { name: 'Marketing' });
    expect(marketing.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByText(
      'Research competitors, create campaigns, repurpose content, and monitor performance.')).toBeTruthy();
  });
});
