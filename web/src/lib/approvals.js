import { api } from '../api';

/**
 * "Don't ask again" for one action, for the agent that asked.
 *
 * It is the owner's own, explicit pre-approval, the same setting the agent's
 * Permissions list shows and can undo, written through the same audited PATCH. It
 * names one action; it does not widen an app or an agent. Destructive actions and
 * anything on the always-approve floor can never be pre-approved, and the server
 * ignores the entry if one were added, so this never lowers the gate that matters.
 */
export async function alwaysAllow(approval) {
  const agentId = approval?.requestedBy?.agentId;
  if (!agentId || !approval.action) return;
  const agent = await api.agent(agentId);
  const preapproved = [...new Set([...(agent.preapproved || []), approval.action])];
  await api.updateAgent(agentId, { preapproved });
}



/** Prefer the server's post-execution approval snapshot over a local status.
 * Agent creation can be created+started, created+deferred, queued, or failed;
 * reducing every answer to `approved` hides the outcome the server persisted. */
export function settledApproval(current, result, approved) {
  return result?.approval || { ...current, status: approved ? 'approved' : 'denied' };
}
