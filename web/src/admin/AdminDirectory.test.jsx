import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    admin: {
      directory: vi.fn(), suspend: vi.fn(), reactivate: vi.fn(), changeRole: vi.fn(),
      resetOnboarding: vi.fn(), archiveMemory: vi.fn(),
    },
  },
}));
import { api } from '../api';
import AdminDirectory from './AdminDirectory';

const MEMBERS = [
  { subject: 'you', role: 'owner', scope: 'org', state: 'active' },
  { subject: 'auth0|teammate', role: 'member', scope: 'org', state: 'active' },
];

describe('AdminDirectory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.admin.directory.mockResolvedValue({ members: MEMBERS });
    api.admin.suspend.mockResolvedValue({});
    api.admin.resetOnboarding.mockResolvedValue({});
    api.admin.archiveMemory.mockResolvedValue({});
  });

  it('lists the members from the directory', async () => {
    render(<AdminDirectory />);
    expect(await screen.findByText('auth0|teammate')).toBeTruthy();
    expect(screen.getByText('you')).toBeTruthy();
  });

  it('does not fire suspend until the typed confirmation matches AND a reason is entered', async () => {
    render(<AdminDirectory />);
    await screen.findByText('auth0|teammate');

    // Open the confirm for the teammate row (index 1 of the Suspend buttons).
    fireEvent.click(screen.getAllByRole('button', { name: 'Suspend' })[1]);
    const dialog = await screen.findByRole('dialog');
    const confirmBtn = within(dialog).getByRole('button', { name: 'Suspend member' });

    // Nothing entered yet: the gate is closed and the call has not been made.
    expect(confirmBtn.disabled).toBe(true);
    expect(api.admin.suspend).not.toHaveBeenCalled();

    // A matching token alone is not enough without a reason.
    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'auth0|teammate' } });
    expect(confirmBtn.disabled).toBe(true);
    fireEvent.click(confirmBtn);
    expect(api.admin.suspend).not.toHaveBeenCalled();

    // A reason without a matching token is also not enough.
    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'wrong' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'off-boarding' } });
    expect(confirmBtn.disabled).toBe(true);
    fireEvent.click(confirmBtn);
    expect(api.admin.suspend).not.toHaveBeenCalled();

    // Both present: the gate opens and the call fires against the right subject.
    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'auth0|teammate' } });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(api.admin.suspend).toHaveBeenCalledWith('auth0|teammate'));
  });

  it('blocks reset-onboarding and archive-memory the same way', async () => {
    render(<AdminDirectory />);
    await screen.findByText('auth0|teammate');

    // Reset onboarding, scoped to the confirm dialog so the row button and the
    // confirm button (same label) are never confused.
    fireEvent.click(screen.getAllByRole('button', { name: 'Reset onboarding' })[1]);
    let dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset onboarding' }));
    expect(api.admin.resetOnboarding).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'auth0|teammate' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'stale setup' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset onboarding' }));
    await waitFor(() => expect(api.admin.resetOnboarding).toHaveBeenCalledWith('auth0|teammate'));

    // Archive memory: same gate.
    fireEvent.click(screen.getAllByRole('button', { name: 'Archive memory' })[1]);
    dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive memory' }));
    expect(api.admin.archiveMemory).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'auth0|teammate' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'privacy request' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive memory' }));
    await waitFor(() => expect(api.admin.archiveMemory).toHaveBeenCalledWith('auth0|teammate'));
  });
});
