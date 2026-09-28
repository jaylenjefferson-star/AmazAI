import { describe, expect, it } from 'vitest';
import { isPublicPath, normalizePublicPath } from './publicRoutes';

describe('public route matching', () => {
  it('treats canonical and trailing-slash policy URLs as the same public route', () => {
    expect(isPublicPath('/terms')).toBe(true);
    expect(isPublicPath('/terms/')).toBe(true);
    expect(isPublicPath('/privacy///')).toBe(true);
  });

  it('keeps signed-in application routes out of the public surface', () => {
    expect(isPublicPath('/billing')).toBe(false);
    expect(isPublicPath('/agents/agent-1')).toBe(false);
  });

  it('normalizes only trailing slashes', () => {
    expect(normalizePublicPath('/acceptable-use/')).toBe('/acceptable-use');
    expect(normalizePublicPath('/')).toBe('/');
  });
});
