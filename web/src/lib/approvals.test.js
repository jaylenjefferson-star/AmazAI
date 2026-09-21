import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ api: { agent: vi.fn(), updateAgent: vi.fn() } }));
import { api } from '../api';
import { alwaysAllow, settledApproval } from './approvals';

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



describe('settledApproval', () => {
  it('keeps the server execution outcome instead of reducing it to approved', () => {
    const server = {
      approval: {
        approvalId: 'a1', status: 'approved', executionStatus: 'created',
        firstTaskStatus: { status: 'deferred', reason: 'over budget' },
      },
    };
    expect(settledApproval({ approvalId: 'a1', status: 'pending' }, server, true))
      .toBe(server.approval);
  });

  it('falls back to the local settled status for older server responses', () => {
    expect(settledApproval({ approvalId: 'a1', status: 'pending' }, {}, false))
      .toEqual({ approvalId: 'a1', status: 'denied' });
  });
});
