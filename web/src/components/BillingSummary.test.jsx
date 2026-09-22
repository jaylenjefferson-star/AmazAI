import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    billing: {
      get: vi.fn(), plans: vi.fn(), ledger: vi.fn(), checkout: vi.fn(), portal: vi.fn(),
    },
  },
}));
import { api } from '../api';
import BillingSummary from './BillingSummary';

const BILLING = {
  balanceUsd: 14.32, creditsRemaining: 754, tier: 'personal',
  subscriptionStatus: 'active', hasCredit: true,
};
const PLANS = {
  currency: 'usd',
  plans: {
    explore: { name: 'AmazAI Explore', description: 'Build your first AI team.',
      priceUsd: 0, creditsPerMonth: 100, interval: 'month' },
    personal: { name: 'AmazAI Personal', description: 'Your work, delegated.',
      priceUsd: 19, creditsPerMonth: 1000, interval: 'month' },
  },
  creditTopUps: [
    { name: '50 Amaz Credits', priceUsd: 50, lookupKey: 'amazai_credits_50' },
  ],
};

describe('BillingSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.billing.get.mockResolvedValue(BILLING);
    api.billing.plans.mockResolvedValue(PLANS);
    api.billing.ledger.mockResolvedValue({ entries: [] });
  });

  it('shows the real balance and current tier once loaded', async () => {
    render(<BillingSummary />);
    expect(await screen.findByText('$14.32')).toBeTruthy();
    expect(screen.getByText('≈ 754 Amaz Credits')).toBeTruthy();
    expect(screen.getByText('Personal')).toBeTruthy();
  });

  it('marks the account\'s current tier and does not offer to switch to it', async () => {
    render(<BillingSummary />);
    await screen.findByText('AmazAI Personal');
    expect(screen.getByText('Current plan', { selector: 'button' }).disabled).toBe(true);
  });

  it('starting checkout for a different plan redirects to the returned url', async () => {
    api.billing.checkout.mockResolvedValue({ url: 'https://checkout.stripe.com/x' });
    render(<BillingSummary />);
    const switchBtn = await screen.findByRole('button', { name: /Switch to AmazAI Explore/ });
    fireEvent.click(switchBtn);
    await waitFor(() => expect(api.billing.checkout).toHaveBeenCalledWith({ planKey: 'explore' }));
  });

  it('a failed checkout attempt shows the error instead of navigating', async () => {
    api.billing.checkout.mockRejectedValue(new Error('Stripe unavailable'));
    render(<BillingSummary />);
    const switchBtn = await screen.findByRole('button', { name: /Switch to AmazAI Explore/ });
    fireEvent.click(switchBtn);
    expect(await screen.findByText('Stripe unavailable')).toBeTruthy();
  });

  it('offers a credit top-up that starts a one-time checkout', async () => {
    api.billing.checkout.mockResolvedValue({ url: 'https://checkout.stripe.com/topup' });
    render(<BillingSummary />);
    const topUp = await screen.findByRole('button', { name: /50 Amaz Credits/ });
    fireEvent.click(topUp);
    await waitFor(() => expect(api.billing.checkout)
      .toHaveBeenCalledWith({ topUpKey: 'amazai_credits_50' }));
  });

  it('manage subscription starts a portal session', async () => {
    api.billing.portal.mockResolvedValue({ url: 'https://billing.stripe.com/x' });
    render(<BillingSummary />);
    const manage = await screen.findByRole('button', { name: 'Manage subscription' });
    fireEvent.click(manage);
    await waitFor(() => expect(api.billing.portal).toHaveBeenCalled());
  });

  it('an account with no billing row yet shows the load error, not a crash', async () => {
    api.billing.get.mockRejectedValue(new Error('not found'));
    render(<BillingSummary />);
    expect(await screen.findByText('not found')).toBeTruthy();
  });
});
