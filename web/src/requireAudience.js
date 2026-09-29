/** Build-time guard. The runtime gate is `authConfigured` in auth0.jsx. */
export function assertAuth0Audience(audience) {
  if (!String(audience ?? '').trim()) {
    throw new Error(
      'VITE_AUTH0_AUDIENCE is required. Set it to the API audience in config/auth0.json (https://api.amazai.co) before building the console.',
    );
  }
}
