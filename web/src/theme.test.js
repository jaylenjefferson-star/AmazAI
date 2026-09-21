import { beforeEach, describe, expect, it } from 'vitest';
import { explicitMode, hasSessionHint, isPublicPath, modeForPath, syncToPath } from './theme';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('which theme a screen shows', () => {
  it('is dark for the signed-in app until the owner chooses otherwise', () => {
    expect(modeForPath('/', true)).toBe('dark');
    expect(modeForPath('/agents/eng', true)).toBe('dark');
  });

  it('keeps the public site light, even for a signed-in person, unless they chose dark', () => {
    expect(isPublicPath('/pricing')).toBe(true);
    expect(modeForPath('/pricing', true)).toBe('light');
    localStorage.setItem('amazai.theme', 'dark');
    expect(modeForPath('/pricing', true)).toBe('dark');
  });

  it('shows a signed-out visitor the light landing page at /, never a flash of dark', () => {
    expect(modeForPath('/', false)).toBe('light');
  });

  it('honours an explicit Light in the app: light mode is a first-class choice', () => {
    localStorage.setItem('amazai.theme', 'light');
    expect(explicitMode()).toBe('light');
    expect(modeForPath('/', true)).toBe('light');
    expect(modeForPath('/agents/eng', true)).toBe('light');
  });

  it('follows the device when they chose "system"', () => {
    localStorage.setItem('amazai.theme', 'system');
    expect(modeForPath('/', true)).toBe('system');
    syncToPath('/', true);
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('navigating changes what is shown, but never what was chosen', () => {
    syncToPath('/pricing', true);
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    syncToPath('/', true);
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('amazai.theme')).toBeNull();
  });

  it('reads a signed-in session from the Auth0 cache', () => {
    expect(hasSessionHint()).toBe(false);
    localStorage.setItem('@@auth0spajs@@::client::aud::openid', '{}');
    expect(hasSessionHint()).toBe(true);
  });
});
