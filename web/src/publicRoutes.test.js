import { describe, expect, it } from 'vitest';
import { SPA_ENTRY_PATHS, isPublicPath, isWelcomePath, normalizePublicPath } from './publicRoutes';

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

  it('treats /welcome/ as setup, not a public marketing page', () => {
    expect(isWelcomePath('/welcome')).toBe(true);
    expect(isWelcomePath('/welcome/')).toBe(true);
    expect(isWelcomePath('/welcome-to-amazai')).toBe(false);
    expect(isPublicPath('/welcome')).toBe(false);
    expect(isPublicPath('/welcome/')).toBe(false);
    expect(SPA_ENTRY_PATHS).toContain('/welcome');
    expect(SPA_ENTRY_PATHS).toEqual(expect.arrayContaining([
      '/billing', '/plans', '/plans/success',
    ]));
    expect(SPA_ENTRY_PATHS.some((path) => isPublicPath(path))).toBe(false);
  });
});
