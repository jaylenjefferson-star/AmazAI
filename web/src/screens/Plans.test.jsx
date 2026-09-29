import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    billing: {
      get: vi.fn(), plans: vi.fn(), checkout: vi.fn(), confirmExplore: vi.fn(),
    },
  },
}));
import { api } from '../api';
import { forgetRegistration } from '../hooks/useRegistration';
import Plans, { PlanSuccess } from './Plans';

const CATALOG = {
  plans: {
    explore: { name: 'AmazAI Explore', description: 'Build your first AI team.',
      priceUsd: 0, creditsPerMonth: 100, interval: 'month' },
    personal: { name: 'AmazAI Personal', description: 'Your work, delegated.',
      priceUsd: 19, creditsPerMonth: 1000, interval: 'month',
      stripePriceId: 'price_1UIc2MC4NrBvP4LBa9KfvJcm' },
  },
  creditTopUps: [
    { name: '50 Amaz Credits', priceUsd: 50, lookupKey: 'amazai_credits_50' },
  ],
};

const TRIAL = {
  balanceUsd: 5, tier: 'trial', registrationIncomplete: true, hasStripeCustomer: false,
};

function renderPlans(path = '/plans') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/plans" element={<Plans />} />
        <Route path="/plans/success" element={<PlanSuccess />} />
        <Route path="/" element={<p>workspace</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Plans', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgetRegistration();
    api.billing.get.mockResolvedValue(TRIAL);
    api.billing.plans.mockResolvedValue(CATALOG);
  });

  afterEach(() => forgetRegistration());

  it('confirms Explore without checkout and unlocks the workspace', async () => {
    api.billing.confirmExplore.mockResolvedValue({
      ...TRIAL, tier: 'explore', registrationIncomplete: false,
    });
    renderPlans();
    expect(await screen.findByRole('heading', { name: 'Choose a plan' })).toBeTruthy();
    expect(screen.getByText('$0')).toBeTruthy();
    expect(screen.getByText('$19')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /50 Amaz Credits/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue with Explore' }));
    await waitFor(() => expect(api.billing.confirmExplore).toHaveBeenCalled());
    expect(api.billing.checkout).not.toHaveBeenCalled();
    expect(await screen.findByText('workspace')).toBeTruthy();
  });

  it('starts signup checkout for a paid plan', async () => {
    api.billing.checkout.mockResolvedValue({ url: 'https://checkout.stripe.com/c/pay_test' });
    renderPlans();
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to checkout' }));
    await waitFor(() => expect(api.billing.checkout).toHaveBeenCalledWith({
      planKey: 'personal', purpose: 'signup',
    }));
  });

  it('says so when checkout was cancelled', async () => {
    renderPlans('/plans?checkout=cancelled');
    expect(await screen.findByText(/No charge was made/)).toBeTruthy();
  });

  it('shows the trial balance from the account, not a hardcoded grant', async () => {
    renderPlans();
    expect(await screen.findByText(/trial balance of \$5/)).toBeTruthy();
  });
});

describe('PlanSuccess', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgetRegistration();
  });

  afterEach(() => forgetRegistration());

  it('stays closed until the account is entitled, then continues', async () => {
    api.billing.get
      .mockResolvedValueOnce({ tier: 'trial', registrationIncomplete: true })
      .mockResolvedValue({ tier: 'personal', registrationIncomplete: false });
    renderPlans('/plans/success');
    expect(await screen.findByRole('heading', { name: 'Confirming payment' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull();
  });

  it('unlocks when the webhook has already named the plan', async () => {
    api.billing.get.mockResolvedValue({ tier: 'pro', registrationIncomplete: false });
    renderPlans('/plans/success');
    expect(await screen.findByRole('heading', { name: 'Plan confirmed' })).toBeTruthy();
    expect(screen.getByText(/Pro is on this account/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('workspace')).toBeTruthy();
  });
});
