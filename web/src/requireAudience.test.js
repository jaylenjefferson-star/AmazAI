import { describe, expect, it } from 'vitest';
import { authConfigured } from './auth0';
import { assertAuth0Audience } from './requireAudience';

describe('Auth0 audience is required', () => {
  it('does not treat a build with domain and client id as configured', () => {
    expect(authConfigured({
      domain: 'dev-msijboy7a85k3chd.us.auth0.com',
      clientId: 'your-auth0-spa-client-id',
      audience: '',
    })).toBe(false);
  });

  it('accepts the API audience from config/auth0.json', () => {
    expect(authConfigured({
      domain: 'dev-msijboy7a85k3chd.us.auth0.com',
      clientId: 'your-auth0-spa-client-id',
      audience: 'https://api.amazai.co',
    })).toBe(true);
  });

  it('fails the production build when the audience is missing', () => {
    expect(() => assertAuth0Audience('')).toThrow(/VITE_AUTH0_AUDIENCE is required/);
    expect(() => assertAuth0Audience('   ')).toThrow(/VITE_AUTH0_AUDIENCE is required/);
    expect(() => assertAuth0Audience('https://api.amazai.co')).not.toThrow();
  });
});
