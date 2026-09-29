import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const settings = vi.fn();
const getAccessTokenSilently = vi.fn();
const logout = vi.fn();

vi.mock('../api', () => ({ api: { settings: (...args) => settings(...args) } }));
vi.mock('../auth0', () => ({
  config: { audience: 'https://api.amazai.co' },
  startLogout: (fn) => fn({ logoutParams: { returnTo: 'origin' } }),
  useAuth0: () => ({ getAccessTokenSilently, logout }),
}));

import EmailGate from './EmailGate';

describe('EmailGate', () => {
  beforeEach(() => {
    settings.mockReset();
    getAccessTokenSilently.mockReset();
    logout.mockReset();
  });

  it('shows a verify-your-email screen for EMAIL_NOT_VERIFIED and stays there', async () => {
    const err = new Error('Verify your email before AmazAI can create your workspace.');
    err.code = 'EMAIL_NOT_VERIFIED';
    settings.mockRejectedValue(err);

    render(<EmailGate><p>workspace</p></EmailGate>);

    expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeTruthy();
    expect(screen.queryByText('workspace')).toBeNull();
  });

  it('renders the workspace once the API accepts the caller', async () => {
    settings.mockResolvedValue({ theme: 'system' });
    render(<EmailGate><p>workspace</p></EmailGate>);
    expect(await screen.findByText('workspace')).toBeTruthy();
  });

  it('refreshes the access token and retries after the person confirms', async () => {
    const err = new Error('Verify your email before AmazAI can create your workspace.');
    err.code = 'EMAIL_NOT_VERIFIED';
    settings.mockRejectedValueOnce(err).mockResolvedValueOnce({ theme: 'system' });
    getAccessTokenSilently.mockResolvedValue('new-token');

    render(<EmailGate><p>workspace</p></EmailGate>);
    fireEvent.click(await screen.findByRole('button', { name: "I've verified my email" }));

    await waitFor(() => expect(screen.getByText('workspace')).toBeTruthy());
    expect(getAccessTokenSilently).toHaveBeenCalledWith({
      cacheMode: 'off',
      authorizationParams: { audience: 'https://api.amazai.co' },
    });
    expect(settings).toHaveBeenCalledTimes(2);
  });

  it('does not treat a different failure as an unverified email', async () => {
    settings.mockRejectedValue(new Error('Having trouble connecting.'));
    render(<EmailGate><p>workspace</p></EmailGate>);
    expect(await screen.findByText('workspace')).toBeTruthy();
  });
});
