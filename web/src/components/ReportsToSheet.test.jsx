import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ api: { updateAgent: vi.fn() } }));
import { api } from '../api';
import { ORG_CHANGED } from '../lib/org';
import ReportsToSheet from './ReportsToSheet';

const bot = (agentId, name, managerId) => ({ agentId, name, managerId, archetype: 'pebble', color: '#7b93ff' });
const TEAM = [bot('chief', 'Chief', null), bot('eng', 'Engineering', 'chief'), bot('qa', 'QA', 'eng'), bot('ops', 'Ops', 'chief')];

const renderIt = (agent, extra = {}) => render(
  <ReportsToSheet agent={agent} agents={TEAM} onClose={extra.onClose || vi.fn()} onChanged={extra.onChanged} />);

describe('ReportsToSheet', () => {
  beforeEach(() => { vi.clearAllMocks(); api.updateAgent.mockResolvedValue({}); });

  it('never offers a Bot itself or anyone on its own team, so a loop cannot be picked', () => {
    renderIt(TEAM[1]);                                    // Engineering; QA reports to it
    const names = screen.getAllByRole('button').map((b) => b.textContent);
    expect(names.some((n) => n.startsWith('Chief'))).toBe(true);
    expect(names.some((n) => n.startsWith('Ops'))).toBe(true);
    expect(names.some((n) => n.startsWith('Engineering'))).toBe(false);
    expect(names.some((n) => n.startsWith('QA'))).toBe(false);
  });

  it('marks where it reports today, out loud as well as with a tick', () => {
    renderIt(TEAM[1]);
    const chief = screen.getByRole('button', { name: /^Chief/ });
    expect(chief.textContent).toContain('Current');
  });

  it('moves a Bot, tells the chart, and closes', async () => {
    const onClose = vi.fn();
    const onChanged = vi.fn();
    const heard = vi.fn();
    window.addEventListener(ORG_CHANGED, heard);
    renderIt(TEAM[1], { onClose, onChanged });
    fireEvent.click(screen.getByRole('button', { name: /^Ops/ }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.updateAgent).toHaveBeenCalledWith('eng', { reportsTo: 'ops' });
    expect(onChanged).toHaveBeenCalled();
    expect(heard).toHaveBeenCalled();
    window.removeEventListener(ORG_CHANGED, heard);
  });

  it('sends "owner" for the top of the chart', async () => {
    renderIt(TEAM[1]);
    fireEvent.click(screen.getByRole('button', { name: /^You/ }));
    await waitFor(() => expect(api.updateAgent).toHaveBeenCalledWith('eng', { reportsTo: 'owner' }));
  });

  it('does nothing but close when the line it is already on is chosen', async () => {
    const onClose = vi.fn();
    renderIt(TEAM[1], { onClose });
    fireEvent.click(screen.getByRole('button', { name: /^Chief/ }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.updateAgent).not.toHaveBeenCalled();
  });

  it('says a lost connection in plain words, and stays open', async () => {
    api.updateAgent.mockRejectedValue(new Error('Load failed'));
    const onClose = vi.fn();
    renderIt(TEAM[1], { onClose });
    fireEvent.click(screen.getByRole('button', { name: /^Ops/ }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Having trouble connecting.');
    expect(alert.textContent).not.toMatch(/load failed/i);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('passes the server\u2019s own reason through when it refuses a move', async () => {
    api.updateAgent.mockRejectedValue(new Error("'qa' already reports up to 'eng'; that would make a loop"));
    renderIt(TEAM[1]);
    fireEvent.click(screen.getByRole('button', { name: /^Ops/ }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('that would make a loop');
  });

  it('says it changes nothing about what a Bot can do', () => {
    renderIt(TEAM[1]);
    expect(screen.getByText(/doesn.t change what any Bot can do/)).toBeTruthy();
  });
});
