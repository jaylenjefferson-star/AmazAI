import { describe, expect, it } from 'vitest';
import {
  formatPlanPrice, homePath, listedTopUps, planEntitled, plansPath, registrationGate,
} from './billing';

describe('billing plan rules', () => {
  it('formats a catalog price without inventing cents', () => {
    expect(formatPlanPrice(0)).toBe('$0');
    expect(formatPlanPrice(19)).toBe('$19');
    expect(formatPlanPrice(19.5)).toBe('$19.50');
  });

  it('gates only an explicit incomplete registration', () => {
    expect(registrationGate(null)).toBe('open');
    expect(registrationGate({})).toBe('open');
    expect(registrationGate({ registrationIncomplete: false })).toBe('open');
    expect(registrationGate({ registrationIncomplete: true })).toBe('incomplete');
  });

  it('treats a named catalog plan as entitled and a trial as not', () => {
    expect(planEntitled({ tier: 'trial', registrationIncomplete: true })).toBe(false);
    expect(planEntitled({ tier: 'trial', registrationIncomplete: false })).toBe(false);
    expect(planEntitled({ tier: 'explore', registrationIncomplete: false })).toBe(true);
    expect(planEntitled({ tier: 'personal', registrationIncomplete: false })).toBe(true);
    expect(planEntitled({ tier: 'personal', registrationIncomplete: true })).toBe(false);
  });

  it('keeps demo query params and drops a stale checkout flag', () => {
    expect(plansPath('?demo=1&fresh=1')).toBe('/plans?demo=1&fresh=1');
    expect(plansPath('?demo=1&checkout=cancelled')).toBe('/plans?demo=1');
    expect(homePath('')).toBe('/');
  });

  it('lists only the two credit packs', () => {
    const packs = listedTopUps({
      creditTopUps: [
        { lookupKey: 'amazai_credits_50' },
        { lookupKey: 'amazai_credits_999' },
        { lookupKey: 'amazai_credits_200' },
      ],
    });
    expect(packs.map((p) => p.lookupKey)).toEqual(['amazai_credits_50', 'amazai_credits_200']);
  });
});
