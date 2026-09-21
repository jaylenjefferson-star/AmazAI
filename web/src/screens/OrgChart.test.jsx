import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ api: { agents: vi.fn() } }));
vi.mock('../auth0', () => ({ useAuth0: () => ({ user: { name: 'Jaylen Brewer' } }) }));
const open = vi.fn();
vi.mock('../components/ContactCard', () => ({ useContacts: () => ({ open }) }));
vi.mock('../presence', () => ({ usePresence: () => ({}) }));
import { api } from '../api';
import { ORG_CHANGED } from '../lib/org';
import OrgChart from './OrgChart';

const bot = (agentId, name, managerId, extra = {}) => ({
  agentId, name, managerId, role: `${name} does the work`, avatar: { shape: 'pebble', color: '#7b93ff' }, state: 'active', ...extra,
});
const TEAM = [
  bot('chief', 'Chief', null, { entrypoint: true, title: 'Chief' }),
  bot('eng', 'Engineering', 'chief', { title: 'Code' }),
  bot('qa', 'QA', 'eng', { title: 'Tests' }),
  bot('ops', 'Cloud Operations', 'chief'),
];

const renderIt = () => render(<MemoryRouter><OrgChart /></MemoryRouter>);

describe('OrgChart', () => {
  beforeEach(() => { vi.clearAllMocks(); api.agents.mockResolvedValue({ agents: TEAM }); });

  it('draws you at the top, Chief beneath, and each Bot under whoever it reports to', async () => {
    renderIt();
    await screen.findByRole('button', { name: /^Chief, Chief/ });
    const tree = screen.getByRole('list', { name: 'Org chart' });
    // Depth is nesting: QA is inside Engineering's branch, Engineering inside Chief's.
    const eng = screen.getByRole('button', { name: /^Engineering/ }).closest('li');
    expect(eng.querySelector('.oc-branch')).toBeTruthy();
    expect(eng.querySelector('.oc-branch').textContent).toContain('QA');
    const chief = screen.getByRole('button', { name: /^Chief/ }).closest('li');
    expect(chief.querySelector('.oc-branch').textContent).toContain('Cloud Operations');
    expect(tree.querySelector('.oc-root').firstElementChild.textContent).toContain('You');
  });

  it('says the rule in words, with Chief named', async () => {
    renderIt();
    expect(await screen.findByText('Every Bot reports to Chief unless you set it otherwise.')).toBeTruthy();
  });

  it('says it is organisation, not authority', async () => {
    renderIt();
    expect(await screen.findByText(/doesn.t change what any Bot can do/)).toBeTruthy();
  });

  it('opens a Bot’s card when it is tapped', async () => {
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: /^Engineering/ }));
    expect(open).toHaveBeenCalledWith('eng');
  });

  it('folds a team away and counts everyone under it', async () => {
    renderIt();
    const fold = await screen.findByRole('button', { name: /Hide the 3 Bots under Chief/ });
    fireEvent.click(fold);
    expect(screen.queryByRole('button', { name: /^Engineering/ })).toBeNull();
    expect(screen.getByRole('button', { name: /Show the 3 Bots under Chief/ }).getAttribute('aria-expanded')).toBe('false');
  });

  it('redraws when a line is changed somewhere else', async () => {
    renderIt();
    await screen.findByRole('button', { name: /^QA/ });
    api.agents.mockResolvedValue({ agents: TEAM.map((a) => (a.agentId === 'qa' ? { ...a, managerId: 'chief' } : a)) });
    act(() => { window.dispatchEvent(new Event(ORG_CHANGED)); });
    await waitFor(() => {
      const eng = screen.getByRole('button', { name: /^Engineering/ }).closest('li');
      expect(eng.querySelector('.oc-branch')).toBeNull();     // QA moved out from under it
    });
  });

  it('says so, and offers the way in, before there is anyone', async () => {
    api.agents.mockResolvedValue({ agents: [] });
    renderIt();
    expect(await screen.findByText('No Bots yet')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'New Bot' }).getAttribute('href')).toBe('/agents/new');
  });

  it('with no Chief yet, says everyone reports to you', async () => {
    api.agents.mockResolvedValue({ agents: [bot('eng', 'Engineering', null)] });
    renderIt();
    expect(await screen.findByText('Everyone here reports to you.')).toBeTruthy();
  });

  it('says what went wrong, in plain words, and can try again', async () => {
    api.agents.mockRejectedValue(new Error('Load failed'));
    renderIt();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).not.toMatch(/load failed/i);
    expect(screen.getByRole('button', { name: /try again|retry/i })).toBeTruthy();
  });
});
