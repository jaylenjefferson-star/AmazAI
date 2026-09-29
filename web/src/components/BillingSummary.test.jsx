import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    billing: {
      get: vi.fn(), plans: vi.fn(), ledger: vi.fn(), checkout: vi.fn(), portal: vi.fn(),
      confirmExplore: vi.fn(),
    },
  },
}));
import { api } from '../api';
import BillingSummary from './BillingSummary';

const BILLING = {
  balanceUsd: 14.32, creditsRemaining: 754, tier: 'personal',
  subscriptionStatus: 'active', hasCredit: true,
  registrationIncomplete: false, hasStripeCustomer: true,
};
const PLANS = {
  currency: 'usd',
  plans: {
    explore: { name: 'AmazAI Explore', description: 'Build your first AI team.',
      priceUsd: 0, creditsPerMonth: 100, interval: 'month',
      stripePriceId: 'price_explore' },
    personal: { name: 'AmazAI Personal', description: 'Your work, delegated.',
      priceUsd: 19, creditsPerMonth: 1000, interval: 'month',
      stripePriceId: 'price_personal' },
    personal_plus: { name: 'AmazAI Personal+', description: 'More capacity for daily work.',
      priceUsd: 39, creditsPerMonth: 2500, interval: 'month',
      stripePriceId: 'price_personal_plus' },
  },
  creditTopUps: [
    { name: '50 Amaz Credits', priceUsd: 50, lookupKey: 'amazai_credits_50',
      stripePriceId: 'price_50' },
    { name: '200 Amaz Credits', priceUsd: 200, lookupKey: 'amazai_credits_200',
      stripePriceId: 'price_200' },
    { name: '999 Amaz Credits', priceUsd: 999, lookupKey: 'amazai_credits_999',
      stripePriceId: 'price_999' },
  ],
};

const renderIt = () => render(<MemoryRouter><BillingSummary /></MemoryRouter>);

describe('BillingSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.billing.get.mockResolvedValue(BILLING);
    api.billing.plans.mockResolvedValue(PLANS);
    api.billing.ledger.mockResolvedValue({ entries: [] });
  });

  it('shows the real balance and current tier once loaded', async () => {
    renderIt();
    expect(await screen.findByText('$14.32')).toBeTruthy();
    expect(screen.getByText('≈ 754 Amaz Credits')).toBeTruthy();
    expect(screen.getByText('Personal')).toBeTruthy();
  });

  it('marks the account\'s current tier and does not offer to switch to it', async () => {
    renderIt();
    await screen.findByText('AmazAI Personal');
    expect(screen.getByText('Current plan', { selector: 'button' }).disabled).toBe(true);
  });

  it('a subscribed account changes plan in the portal, not a second checkout', async () => {
    api.billing.portal.mockResolvedValue({ url: 'https://billing.stripe.com/x' });
    renderIt();
    const change = await screen.findByRole('button', { name: 'Change plan' });
    fireEvent.click(change);
    await waitFor(() => expect(api.billing.portal).toHaveBeenCalled());
    expect(api.billing.checkout).not.toHaveBeenCalled();
    expect(api.billing.confirmExplore).not.toHaveBeenCalled();
  });

  it('an account with no Stripe customer checks out a paid plan and confirms Explore', async () => {
    api.billing.get.mockResolvedValue({
      ...BILLING, tier: 'trial', hasStripeCustomer: false, subscriptionStatus: null,
      registrationIncomplete: true,
    });
    api.billing.checkout.mockResolvedValue({ url: 'https://checkout.stripe.com/x' });
    api.billing.confirmExplore.mockResolvedValue({
      ...BILLING, tier: 'explore', hasStripeCustomer: false, registrationIncomplete: false,
    });
    renderIt();
    const [personalCheckout] = await screen.findAllByRole('button', { name: 'Continue to checkout' });
    fireEvent.click(personalCheckout);
    await waitFor(() => expect(api.billing.checkout).toHaveBeenCalledWith({
      planKey: 'personal', purpose: 'billing',
    }));

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Explore' }));
    await waitFor(() => expect(api.billing.confirmExplore).toHaveBeenCalled());
    expect(api.billing.checkout).toHaveBeenCalledTimes(1);
  });

  it('a failed checkout attempt shows the error instead of navigating', async () => {
    api.billing.get.mockResolvedValue({
      ...BILLING, tier: 'trial', hasStripeCustomer: false, subscriptionStatus: null,
    });
    api.billing.checkout.mockRejectedValue(new Error('Stripe unavailable'));
    renderIt();
    const [personalCheckout] = await screen.findAllByRole('button', { name: 'Continue to checkout' });
    fireEvent.click(personalCheckout);
    expect(await screen.findByText('Stripe unavailable')).toBeTruthy();
  });

  it('offers the two credit packs and ignores any other top-up', async () => {
    api.billing.checkout.mockResolvedValue({ url: 'https://checkout.stripe.com/topup' });
    renderIt();
    expect(await screen.findByRole('button', { name: /50 Amaz Credits/ })).toBeTruthy();
    const twoHundred = screen.getByRole('button', { name: /200 Amaz Credits/ });
    expect(screen.queryByRole('button', { name: /999 Amaz Credits/ })).toBeNull();
    fireEvent.click(twoHundred);
    await waitFor(() => expect(api.billing.checkout)
      .toHaveBeenCalledWith({ topUpKey: 'amazai_credits_200', purpose: 'billing' }));
  });

  it('an account with no billing row yet shows the load error, not a crash', async () => {
    api.billing.get.mockRejectedValue(new Error('not found'));
    renderIt();
    expect(await screen.findByText('not found')).toBeTruthy();
  });
});
