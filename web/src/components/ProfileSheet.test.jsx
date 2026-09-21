import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ api: { saveSettings: vi.fn().mockResolvedValue({}), settings: vi.fn().mockResolvedValue({}) } }));
vi.mock('../auth0', () => ({
  useAuth0: () => ({ user: { name: 'Jaylen', email: 'j@example.com' }, logout: vi.fn() }),
  startLogout: vi.fn(), config: {}, operatorFirstName: () => 'Jaylen',
}));
import { api } from '../api';
import ProfileSheet from './ProfileSheet';

describe('ProfileSheet appearance', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    document.documentElement.setAttribute('data-theme', 'dark');
  });

  it('switches to light immediately and saves it to the account, so Settings agrees', () => {
    render(<MemoryRouter><ProfileSheet onClose={vi.fn()} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(localStorage.getItem('amazai.theme')).toBe('light');
    expect(api.saveSettings).toHaveBeenCalledWith({ theme: 'light' });
  });

  it('offers dark, light and follow-the-device', () => {
    render(<MemoryRouter><ProfileSheet onClose={vi.fn()} /></MemoryRouter>);
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Dark', 'Light', 'Auto']);
  });
});
