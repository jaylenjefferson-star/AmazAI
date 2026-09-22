import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    agents: vi.fn(),
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

// The roster the console loads so archive-memory can target a REAL agent id
// (the Bot's slugged name, e.g. 'chief'), never the operator's user subject.
const AGENTS = [
  { agentId: 'chief', name: 'Chief', entrypoint: true },
  { agentId: 'worker', name: 'Cloud Ops', entrypoint: false },
];

describe('AdminDirectory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.admin.directory.mockResolvedValue({ members: MEMBERS });
    api.agents.mockResolvedValue({ agents: AGENTS });
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

    // Both present: the gate opens and the call fires against the right
    // subject, carrying the typed reason so the audit detail records why.
    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'auth0|teammate' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'off-boarding' } });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(api.admin.suspend).toHaveBeenCalledWith('auth0|teammate', 'off-boarding'));
  });

  it('sends NO agent id for reset (server resolves the entrypoint) and a REAL agent id for archive', async () => {
    render(<AdminDirectory />);
    await screen.findByText('auth0|teammate');
    await screen.findByText('Cloud Ops');

    // reset-onboarding acts on the entrypoint Bot; there is exactly one, so one
    // Reset button. archive-memory is per-Bot, so one Archive button per Bot.
    expect(screen.getAllByRole('button', { name: 'Reset onboarding' }).length).toBe(1);
    expect(screen.getAllByRole('button', { name: 'Archive memory' }).length).toBe(AGENTS.length);

    // Reset onboarding, scoped to the confirm dialog so the row button and the
    // confirm button (same label) are never confused. The confirm token is the
    // fixed 'reset' word, not a user subject.
    fireEvent.click(screen.getByRole('button', { name: 'Reset onboarding' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset onboarding' }));
    expect(api.admin.resetOnboarding).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'reset' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'stale setup' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset onboarding' }));
    // The UI sends ONLY the reason -- no id. A regression to passing a user
    // subject as the id would fail this exact-arguments assertion.
    await waitFor(() => expect(api.admin.resetOnboarding).toHaveBeenCalledWith('stale setup'));
  });

  it('archives the specific Bot the operator picks, sending that Bot\u2019s real agent id', async () => {
    render(<AdminDirectory />);
    await screen.findByText('Cloud Ops');

    // Pick the second Bot (Cloud Ops -> agentId 'worker'). Its Archive button
    // is the one that fires with the real agent id, never a user subject.
    fireEvent.click(screen.getAllByRole('button', { name: 'Archive memory' })[1]);
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive memory' }));
    expect(api.admin.archiveMemory).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText('Confirmation'), { target: { value: 'Cloud Ops' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'privacy request' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive memory' }));
    await waitFor(() => expect(api.admin.archiveMemory).toHaveBeenCalledWith('worker', 'privacy request'));
  });
});
