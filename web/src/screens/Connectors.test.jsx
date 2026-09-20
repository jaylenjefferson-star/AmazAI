import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    connectorCatalog: vi.fn(), connectorApps: vi.fn(),
    connectors: vi.fn(), connectorAccounts: vi.fn(),
  },
}));
import { api } from '../api';
import Connectors from './Connectors';

const SLACK = {
  connectorId: 'pipedream:slack', app: 'slack', name: 'Slack',
  description: 'Read channels, post with approval.', actions: [{ tool: 'slack.read' }],
};

describe('Connectors', () => {
  beforeEach(() => {
    api.connectorCatalog.mockResolvedValue({ catalog: [SLACK] });
    api.connectorApps.mockResolvedValue({ apps: [], pageInfo: {} });
    api.connectors.mockResolvedValue({ connectors: [] });
    api.connectorAccounts.mockResolvedValue({ accounts: [] });
  });

  it('still shows our own catalog when the third-party app list fails', async () => {
    // The catalog is static on the server and needs no network. It used to sit
    // behind a Promise.all with the Pipedream calls, so one slow or failing
    // call left the page reading "Loading your connector catalog…" forever.
    api.connectorApps.mockRejectedValue(new Error('Load failed'));
    api.connectorAccounts.mockRejectedValue(new Error('Load failed'));

    render(<Connectors embedded />);

    expect(await screen.findByText('Slack', {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.getByText(/apps: Load failed/)).toBeTruthy();
    expect(screen.queryByText(/Loading your connector catalog/)).toBeNull();
  });

  it('says so, rather than "loading", when the catalog really is empty', async () => {
    api.connectorCatalog.mockResolvedValue({ catalog: [] });

    render(<Connectors embedded />);

    expect(await screen.findByText('No connectors are available yet.', {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.queryByText(/Loading your connector catalog/)).toBeNull();
  });
});
