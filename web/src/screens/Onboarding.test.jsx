import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mockNavigate,
}));
vi.mock('../api', () => ({ api: { createAgent: vi.fn(), saveSettings: vi.fn() } }));
vi.mock('../auth0', () => ({
  useAuth0: () => ({ user: { given_name: 'Jaylen' } }),
  operatorFirstName: () => 'Jaylen',
}));
const rememberSetupDone = vi.fn();
vi.mock('../hooks/useFirstRun', () => ({ rememberSetupDone: () => rememberSetupDone() }));

import { api } from '../api';
import Onboarding from './Onboarding';

const renderIt = () => render(<MemoryRouter><Onboarding /></MemoryRouter>);

async function clickThroughToTheLastStep() {
  renderIt();
  // Step 1: workspace name is prefilled and valid.
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  // Step 2: Bot name is prefilled ('Chief') and valid.
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  // Step 3: the promises step, always continuable.
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  // Step 4: the finish button.
  return screen.getByRole('button', { name: /Meet Chief/ });
}

function acceptPolicies() {
  fireEvent.click(screen.getByRole('checkbox', { name: /I agree to the Terms of Use/i }));
}

describe('Onboarding', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('a normal create-Bot success navigates straight into the conversation', async () => {
    api.createAgent.mockResolvedValue({ agentId: 'chief' });
    api.saveSettings.mockResolvedValue({});
    const finishBtn = await clickThroughToTheLastStep();
    expect(finishBtn.disabled).toBe(true);
    acceptPolicies();

    await act(async () => { fireEvent.click(finishBtn); });

    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({
      onboarded: true,
      legalAcceptance: { version: '2026.09', accepted: true },
    }));
    expect(rememberSetupDone).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith('/agents/chief', { replace: true });
  });

  it('a still-provisioning account runtime retries instead of failing on the first try', async () => {
    api.createAgent
      .mockRejectedValueOnce(new Error(
        'the account runtime could not be provisioned (RuntimeUnavailable: '
        + 'the account harness is still CREATING; retry this request)'))
      .mockResolvedValueOnce({ agentId: 'chief' });
    api.saveSettings.mockResolvedValue({});
    const finishBtn = await clickThroughToTheLastStep();
    acceptPolicies();

    await act(async () => { fireEvent.click(finishBtn); });
    // The retry message, not a raw exception, is what a brand-new person sees.
    expect(screen.getByText(/still starting up/)).toBeTruthy();
    expect(screen.queryByText(/RuntimeUnavailable/)).toBeNull();

    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });

    expect(api.createAgent).toHaveBeenCalledTimes(2);
    expect(api.createAgent.mock.calls[0][1]).toBe(api.createAgent.mock.calls[1][1]);
    expect(mockNavigate).toHaveBeenCalledWith('/agents/chief', { replace: true });
  });

  it('a gateway timeout is retried the same way as a still-creating harness', async () => {
    api.createAgent
      .mockRejectedValueOnce(new Error('504 Gateway Timeout'))
      .mockRejectedValueOnce(new Error('Internal Server Error'))
      .mockResolvedValueOnce({ agentId: 'chief' });
    api.saveSettings.mockResolvedValue({});
    const finishBtn = await clickThroughToTheLastStep();
    acceptPolicies();

    await act(async () => { fireEvent.click(finishBtn); });
    expect(screen.getByText(/still starting up/)).toBeTruthy();

    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });

    expect(api.createAgent).toHaveBeenCalledTimes(3);
    expect(api.createAgent.mock.calls[0][1]).toBe(api.createAgent.mock.calls[2][1]);
    expect(mockNavigate).toHaveBeenCalledWith('/agents/chief', { replace: true });
  });

  it('a missing model id is not retried', async () => {
    api.createAgent.mockRejectedValue(new Error(
      'no modelId resolved for tier \'balanced\'; run scripts/resolve_models.py'));
    const finishBtn = await clickThroughToTheLastStep();
    acceptPolicies();

    await act(async () => { fireEvent.click(finishBtn); });

    expect(api.createAgent).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/no modelId resolved/)).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('a non-retriable error is shown immediately, with no retry delay', async () => {
    api.createAgent.mockRejectedValue(new Error('a Bot with the id \'chief\' already exists'));
    const finishBtn = await clickThroughToTheLastStep();
    acceptPolicies();

    await act(async () => { fireEvent.click(finishBtn); });

    expect(api.createAgent).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/already exists/)).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('exhausting every retry still surfaces the last error rather than hanging forever', async () => {
    api.createAgent.mockRejectedValue(new Error(
      'the account runtime could not be provisioned (RuntimeUnavailable: '
      + 'the account harness is still CREATING; retry this request)'));
    const finishBtn = await clickThroughToTheLastStep();
    acceptPolicies();

    await act(async () => { fireEvent.click(finishBtn); });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000 * 20); });

    expect(api.createAgent).toHaveBeenCalledTimes(20);
    expect(screen.getByText(/still CREATING/)).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
