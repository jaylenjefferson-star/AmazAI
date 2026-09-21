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



describe('agent proposal first task', () => {
  it('shows the complete bound first task before it can be approved', () => {
    const firstTask = 'Review the current operating plan.\n'.repeat(20) + 'DONE MARKER';
    const approval = {
      ...base, action: 'agent.create', policy: { rule: 'floor', matched: 'agent.create' },
      arguments: {
        name: 'Janeisha Carter', title: 'AI Strategy', role: 'Runs execution.',
        description: 'Coordinates the company.', firstTask,
      },
    };
    const { container } = render(<ApprovalCard approval={approval} onDecide={vi.fn()} />);
    const task = container.querySelector('dd.proposal-task');
    expect(task).toBeTruthy();
    expect(task.textContent).toBe(firstTask);
    expect(task.textContent).toContain('DONE MARKER');
    expect(task.textContent).not.toContain('…');
    expect(screen.getByText('AI Strategy')).toBeTruthy();
  });
});



describe('agent proposal execution outcome', () => {
  const agentApproval = {
    ...base, action: 'agent.create', status: 'approved',
    policy: { rule: 'floor', matched: 'agent.create' },
    arguments: { name: 'Janeisha Carter', role: 'Runs execution.' },
    executionStatus: 'created',
  };

  it('shows that the first task started', () => {
    render(<ApprovalCard approval={{
      ...agentApproval, firstTaskStatus: { status: 'started', runId: 'r-child' },
    }} onDecide={vi.fn()} />);
    expect(screen.getByRole('status').textContent).toBe('Bot created. First task started.');
  });

  it('shows a deferred first task and its server reason', () => {
    render(<ApprovalCard approval={{
      ...agentApproval, firstTaskStatus: { status: 'deferred', reason: 'over budget' },
    }} onDecide={vi.fn()} />);
    expect(screen.getByRole('status').textContent)
      .toBe('Bot created. First task is waiting: over budget');
  });

  it('shows a queued retry instead of claiming work started', () => {
    render(<ApprovalCard approval={{
      ...agentApproval, firstTaskStatus: { status: 'queued', runId: 'r-child' },
    }} onDecide={vi.fn()} />);
    expect(screen.getByRole('status').textContent)
      .toBe('Bot created. First task is queued for retry.');
  });

  it('shows creation failure from server execution, not model prose', () => {
    render(<ApprovalCard approval={{
      ...agentApproval, executionStatus: 'failed', executionError: 'RuntimeError: unavailable',
    }} onDecide={vi.fn()} />);
    expect(screen.getByRole('status').textContent)
      .toBe('Creation failed: RuntimeError: unavailable');
  });
});
