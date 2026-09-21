import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ApprovalCard from './ApprovalCard';

const base = {
  approvalId: 'a1', runId: 'r1', status: 'pending', action: 'GMAIL_SEND_EMAIL',
  arguments: { to: 'x@example.com' }, expiresAt: new Date(Date.now() + 600000).toISOString(),
  requestedBy: { agentId: 'chief' }, reversible: null,
};
const write = { ...base, policy: { rule: 'default', matched: 'write', reason: 'write capability without a pre-approved rule' } };

describe('ApprovalCard "don\'t ask again"', () => {
  it('is offered for an ordinary write, and off by default', () => {
    render(<ApprovalCard approval={write} onDecide={vi.fn()} />);
    expect(screen.getByLabelText(/Don.t ask again for/).checked).toBe(false);
  });

  it('is never offered where it could not be honoured', () => {
    const { rerender } = render(<ApprovalCard approval={{ ...base, policy: { rule: 'floor', matched: 'email.send' } }} onDecide={vi.fn()} />);
    expect(screen.queryByLabelText(/Don.t ask again/)).toBeNull();
    rerender(<ApprovalCard approval={{ ...write, reversible: false }} onDecide={vi.fn()} />);
    expect(screen.queryByLabelText(/Don.t ask again/)).toBeNull();
    rerender(<ApprovalCard approval={{ ...base, policy: { rule: 'capability', matched: 'destructive' } }} onDecide={vi.fn()} />);
    expect(screen.queryByLabelText(/Don.t ask again/)).toBeNull();
  });

  it('approves without remembering unless the box is ticked', async () => {
    const onDecide = vi.fn().mockResolvedValue();
    render(<ApprovalCard approval={write} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(onDecide).toHaveBeenCalledWith(true, undefined, { always: false }));
  });

  it('remembers only when approving, never when denying', async () => {
    const onDecide = vi.fn().mockResolvedValue();
    render(<ApprovalCard approval={write} onDecide={onDecide} />);
    fireEvent.click(screen.getByLabelText(/Don.t ask again/));
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(onDecide).toHaveBeenCalledWith(false, undefined, { always: false }));
  });

  it('asks the server to remember when approving with the box ticked', async () => {
    const onDecide = vi.fn().mockResolvedValue();
    render(<ApprovalCard approval={write} onDecide={onDecide} />);
    fireEvent.click(screen.getByLabelText(/Don.t ask again/));
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(onDecide).toHaveBeenCalledWith(true, undefined, { always: true }));
  });
});
