import { describe, expect, it } from 'vitest';
import { isRetryableProvisionError } from './provisionRetry';

describe('isRetryableProvisionError', () => {
  it('retries the server sentence and a gateway timeout', () => {
    expect(isRetryableProvisionError(
      'the account harness is still CREATING; retry this request',
    )).toBe(true);
    expect(isRetryableProvisionError('504 Gateway Timeout')).toBe(true);
    expect(isRetryableProvisionError('502 Bad Gateway')).toBe(true);
    expect(isRetryableProvisionError('503 Service Unavailable')).toBe(true);
    expect(isRetryableProvisionError('Internal Server Error')).toBe(true);
    expect(isRetryableProvisionError('Endpoint request timed out')).toBe(true);
    expect(isRetryableProvisionError('Load failed')).toBe(true);
    expect(isRetryableProvisionError('Failed to fetch')).toBe(true);
  });

  it('does not retry a name conflict or a missing model id', () => {
    expect(isRetryableProvisionError("a Bot with the id 'chief' already exists")).toBe(false);
    expect(isRetryableProvisionError(
      "no modelId resolved for tier 'balanced'; run scripts/resolve_models.py",
    )).toBe(false);
  });
});
