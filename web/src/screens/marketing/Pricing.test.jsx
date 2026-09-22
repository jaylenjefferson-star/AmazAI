import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api', () => ({ api: { billing: { checkout: vi.fn() } } }));
const mockLoginWithRedirect = vi.fn();
let mockIsAuthenticated = false;
vi.mock('../../auth0', () => ({
  useAuth0: () => ({ isAuthenticated: mockIsAuthenticated, loginWithRedirect: mockLoginWithRedirect }),
  startLogin: (loginWithRedirect, opts) => loginWithRedirect(opts),
}));

import { api } from '../../api';
import Pricing from './Pricing';

const renderIt = () => render(<MemoryRouter><Pricing /></MemoryRouter>);

describe('Pricing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIsAuthenticated = false;
  });

  it('a signed-out visitor gets Universal Login signup, not a checkout call', async () => {
    renderIt();
    fireEvent.click(screen.getAllByRole('button', { name: 'Get started' })[1]); // Personal
    expect(mockLoginWithRedirect).toHaveBeenCalledWith(
      expect.objectContaining({ signup: true, returnTo: '/welcome' }));
    expect(api.billing.checkout).not.toHaveBeenCalled();
  });

  it('a signed-in visitor starts checkout for the exact tier clicked', async () => {
    mockIsAuthenticated = true;
    api.billing.checkout.mockResolvedValue({ url: 'https://checkout.stripe.com/x' });
    renderIt();
    fireEvent.click(screen.getAllByRole('button', { name: 'Get started' })[2]); // Personal+
    await waitFor(() => expect(api.billing.checkout)
      .toHaveBeenCalledWith({ planKey: 'personal_plus' }));
    expect(mockLoginWithRedirect).not.toHaveBeenCalled();
  });

  it('a failed checkout attempt shows the error inline', async () => {
    mockIsAuthenticated = true;
    api.billing.checkout.mockRejectedValue(new Error('Stripe unavailable'));
    renderIt();
    fireEvent.click(screen.getAllByRole('button', { name: 'Get started' })[0]); // Explore
    expect(await screen.findByText('Stripe unavailable')).toBeTruthy();
  });
});
