import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  api: {
    agentOptions: vi.fn(), connectors: vi.fn(), createAgent: vi.fn(), connectorApps: vi.fn(),
    connectToken: vi.fn(), installConnector: vi.fn(), revokeConnector: vi.fn(),
  },
}));
vi.mock('../auth0', () => ({
  useAuth0: () => ({ user: { name: 'Jaylen Brewer' } }),
  operatorFirstName: () => 'Jaylen',
}));
import { api } from '../api';
import NewAgent from './NewAgent';

const OPTIONS = {
  colors: ['#d94f4f', '#e88a3d', '#e9b04a', '#5fc48a', '#3f9f92', '#3f6fdc', '#8b5cf6', '#e0407f', '#8a6a4e', '#8a8f9c'],
  modelTiers: [{ key: 'fast' }, { key: 'balanced' }, { key: 'deep' }],
  workingStyles: ['collaborative', 'concise'],
  defaultModelTier: 'balanced', limits: { maxMonthlyUsd: 500 },
};
const GMAIL = { connectorId: 'composio:gmail', app: 'gmail', name: 'Gmail' };
const SLACK = { connectorId: 'composio:slack', app: 'slack', name: 'Slack' };

const renderIt = () => render(<MemoryRouter initialEntries={['/agents/new']}><NewAgent /></MemoryRouter>);

async function fill(name, role) {
  fireEvent.change(await screen.findByLabelText('Name'), { target: { value: name } });
  fireEvent.change(screen.getByPlaceholderText('Sr Director, Head of Ops'), { target: { value: role } });
}

describe('NewAgent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.agentOptions.mockResolvedValue(OPTIONS);
    api.connectors.mockResolvedValue({ connectors: [GMAIL, SLACK] });
    api.createAgent.mockResolvedValue({ agentId: 'tanzie' });
  });

  it('will not create until it has a name and a role, and says which is missing', async () => {
    renderIt();
    const create = await screen.findByRole('button', { name: 'Create agent' });
    expect(create.disabled).toBe(true);
    expect(screen.getByText('Give it a name')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Tanzie' } });
    expect(screen.getByText('Add a role to continue')).toBeTruthy();
  });

  it('sends the identity as the person wrote it, with one idempotency key', async () => {
    renderIt();
    await fill('Tanzie', 'Sr Director, Head of Ops');
    fireEvent.change(screen.getByPlaceholderText('Operations'), { target: { value: 'Operations' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Tanzie' }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalledTimes(1));
    const [body, key] = api.createAgent.mock.calls[0];
    expect(body).toMatchObject({ name: 'Tanzie', title: 'Operations', role: 'Sr Director, Head of Ops', operatorName: 'Jaylen' });
    expect(key).toMatch(/^create-/);
  });

  it('keeps the server default (every connected app) unless one is switched off', async () => {
    // Sending an explicit list, even a full one, would replace the server's
    // default; the default is what makes a Bot made later start with connected apps.
    renderIt();
    await fill('Tanzie', 'Head of Ops');
    fireEvent.click(screen.getByRole('button', { name: 'Create Tanzie' }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect('grants' in api.createAgent.mock.calls[0][0]).toBe(false);
  });

  it('sends exactly the apps left on when one is switched off', async () => {
    renderIt();
    await fill('Tanzie', 'Head of Ops');
    fireEvent.click(screen.getByRole('button', { name: /Capabilities/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Slack/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Tanzie' }));
    await waitFor(() => expect(api.createAgent).toHaveBeenCalled());
    expect(api.createAgent.mock.calls[0][0].grants).toEqual([
      { connectorId: 'composio:gmail', capability: 'admin', allowedTools: ['*'] }]);
  });

  it('says what happened in plain words, and keeps everything typed, when creation fails', async () => {
    api.createAgent.mockRejectedValue(new Error('Load failed'));
    renderIt();
    await fill('Tanzie', 'Head of Ops');
    fireEvent.click(screen.getByRole('button', { name: 'Create Tanzie' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain("Couldn't create Tanzie. Nothing was lost. Try again.");
    expect(alert.textContent).not.toMatch(/load failed/i);
    expect(screen.getByLabelText('Name').value).toBe('Tanzie');
  });
});
