/**
 * Whether Meet-first-Bot should try create again.
 *
 * The server says "retry this request" when the account harness is still
 * coming up. API Gateway does not. A 30s kill comes back as 502/503/504,
 * "Internal Server Error", or a browser "Load failed" / "Failed to fetch",
 * and matching only the server's sentence made that look final.
 *
 * A missing model id is not retryable: the next attempt fails the same way
 * until the platform registry has a row.
 */
const GATEWAY = /^(?:502|503|504)\b|service unavailable|gateway time-?out|endpoint request timed out|internal server error|load failed|failed to fetch|networkerror|network request failed/i;

export function isRetryableProvisionError(message) {
  const text = String(message || '');
  if (/no modelId resolved/i.test(text)) return false;
  if (/retry this request/i.test(text)) return true;
  if (/still (creating|being provisioned)/i.test(text)) return true;
  return GATEWAY.test(text);
}
