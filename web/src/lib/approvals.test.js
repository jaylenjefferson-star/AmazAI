import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ api: { agent: vi.fn(), updateAgent: vi.fn() } }));
import { api } from '../api';
import { alwaysAllow } from './approvals';

describe('alwaysAllow', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('adds exactly the one action to what that agent already had pre-approved', async () => {
    api.agent.mockResolvedValue({ preapproved: ['SLACK_SEND_MESSAGE'] });
    await alwaysAllow({ action: 'GMAIL_SEND_EMAIL', requestedBy: { agentId: 'chief' } });
    expect(api.updateAgent).toHaveBeenCalledWith('chief', { preapproved: ['SLACK_SEND_MESSAGE', 'GMAIL_SEND_EMAIL'] });
  });

  it('does not duplicate one that is already there', async () => {
    api.agent.mockResolvedValue({ preapproved: ['GMAIL_SEND_EMAIL'] });
    await alwaysAllow({ action: 'GMAIL_SEND_EMAIL', requestedBy: { agentId: 'chief' } });
    expect(api.updateAgent).toHaveBeenCalledWith('chief', { preapproved: ['GMAIL_SEND_EMAIL'] });
  });

  it('does nothing when it cannot tell which agent asked', async () => {
    await alwaysAllow({ action: 'GMAIL_SEND_EMAIL' });
    expect(api.updateAgent).not.toHaveBeenCalled();
  });
});
