import { describe, expect, it } from 'vitest';
import { SPA_REWRITE, isSpaIndexFallback, mergeSpaRewrite } from './spaRewrite';

describe('Amplify SPA rewrite', () => {
  it('replaces a 404 index.html rule with a 200 rewrite and keeps other rules', () => {
    const merged = mergeSpaRewrite([
      { source: 'https://www.amazai.co', target: 'https://amazai.co', status: '301' },
      { source: '/<*>', target: '/index.html', status: '404' },
    ]);
    expect(merged[0]).toEqual({
      source: 'https://www.amazai.co', target: 'https://amazai.co', status: '301',
    });
    expect(merged[1]).toEqual(SPA_REWRITE);
    expect(merged).toHaveLength(2);
  });

  it('is idempotent when the 200 rewrite is already present', () => {
    const once = mergeSpaRewrite([SPA_REWRITE]);
    const twice = mergeSpaRewrite(once);
    expect(twice).toEqual([SPA_REWRITE]);
  });

  it('does not treat a 301 as an index fallback', () => {
    expect(isSpaIndexFallback({
      source: '/welcome', target: '/welcome/', status: '301',
    })).toBe(false);
    expect(isSpaIndexFallback({
      source: '/<*>', target: '/index.html', status: '404-200',
    })).toBe(true);
  });
});
