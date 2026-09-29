import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../auth0', () => ({
  configured: false,
  isOwner: () => false,
  useAuth0: () => ({
    isLoading: false,
    isAuthenticated: true,
    error: null,
    user: { sub: 'auth0|someone', email: 'someone@example.com' },
    loginWithRedirect: vi.fn(),
    logout: vi.fn(),
  }),
  startLogin: vi.fn(),
  startLogout: vi.fn(),
}));

import AuthGate from './AuthGate';

describe('AuthGate without an API audience', () => {
  it('shows a config error instead of the signed-in workspace', () => {
    render(<AuthGate><p>workspace</p></AuthGate>);
    expect(screen.getByRole('heading', { name: 'Sign-in is not configured' })).toBeTruthy();
    expect(screen.getByText(/VITE_AUTH0_AUDIENCE/)).toBeTruthy();
    expect(screen.queryByText('workspace')).toBeNull();
  });
});
