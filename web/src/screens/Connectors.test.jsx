import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    connectorApps: vi.fn(), connectors: vi.fn(), connectToken: vi.fn(),
    installConnector: vi.fn(), revokeConnector: vi.fn(),
  },
}));
import { api } from '../api';
import Connectors from './Connectors';

const GMAIL = { slug: 'gmail', name: 'Gmail', description: 'Email.', logo: '' };
const SLACK = { slug: 'slack', name: 'Slack', description: 'Team chat.', logo: '' };

describe('Connectors', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.connectorApps.mockResolvedValue({ apps: [GMAIL, SLACK], pageInfo: { end_cursor: '' } });
    api.connectors.mockResolvedValue({ connectors: [] });
  });

  it('lists every app Composio offers, with no short list in front of it', async () => {
    render(<Connectors embedded />);
    expect(await screen.findByText('Gmail')).toBeTruthy();
    expect(screen.getByText('Slack')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Connect' })).toHaveLength(2);
  });

  it('still shows what is already connected when the third-party list fails', async () => {
    // Our own installed list needs no third party. It used to sit behind a
    // Promise.all with the app list, so one failing call hid everything.
    api.connectorApps.mockRejectedValue(new Error('Load failed'));
    api.connectors.mockResolvedValue({ connectors: [{ connectorId: 'composio:slack', app: 'slack', name: 'Slack' }] });
    render(<Connectors embedded />);
    expect(await screen.findByText('Slack')).toBeTruthy();
    expect(screen.getByText('Connected. Your Bots can use it.')).toBeTruthy();
  });

  it('never shows a raw transport error to a person', async () => {
    api.connectorApps.mockRejectedValue(new Error('Load failed'));
    render(<Connectors embedded />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Having trouble loading apps.');
    expect(alert.textContent).not.toMatch(/load failed/i);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('opens the sign-in page, then installs when the person comes back', async () => {
    api.connectToken.mockResolvedValue({ connectLinkUrl: 'https://connect.composio.dev/link/x' });
    api.installConnector.mockResolvedValue({ connectorId: 'composio:gmail', app: 'gmail', name: 'Gmail' });
    const open = vi.spyOn(window, 'open').mockReturnValue(null);

    render(<Connectors embedded />);
    await screen.findByText('Gmail');
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect' })[0]);

    await waitFor(() => expect(open).toHaveBeenCalledWith(
      'https://connect.composio.dev/link/x', '_blank', 'noopener,noreferrer'));
    expect(api.connectToken).toHaveBeenCalledWith('composio:gmail');
    expect(api.installConnector).not.toHaveBeenCalled();   // nothing is installed by asking for a link

    fireEvent.click(await screen.findByRole('button', { name: "I'm done" }));
    await waitFor(() => expect(api.installConnector).toHaveBeenCalledWith('composio:gmail'));
    expect(await screen.findByText('Gmail is connected. Your Bots can use it now.')).toBeTruthy();
    open.mockRestore();
  });

  it('says nothing alarming when they come back before finishing signing in', async () => {
    api.connectToken.mockResolvedValue({ connectLinkUrl: 'https://connect.composio.dev/link/x' });
    api.installConnector.mockRejectedValue(new Error('gmail is not connected yet. Finish signing in on the connect page, then try again.'));
    vi.spyOn(window, 'open').mockReturnValue(null);
    render(<Connectors embedded />);
    await screen.findByText('Gmail');
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect' })[0]);
    fireEvent.click(await screen.findByRole('button', { name: "I'm done" }));
    await waitFor(() => expect(api.installConnector).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
