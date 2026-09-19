import {
  CognitoUser,
  CognitoUserPool,
  AuthenticationDetails,
} from 'amazon-cognito-identity-js';

const USER_POOL_ID = import.meta.env.VITE_USER_POOL_ID;
const CLIENT_ID = import.meta.env.VITE_USER_POOL_CLIENT_ID;

/** True when the build was given a Cognito pool to talk to at all. */
export const configured = Boolean(USER_POOL_ID && CLIENT_ID);

/**
 * The pool is built on first use, not at import time.
 *
 * CognitoUserPool throws from its constructor when either id is missing, and
 * a throw at module scope takes the whole bundle down before React mounts —
 * a blank white page with the real cause only in the devtools console. A
 * console deployed with an unfilled .env is a likely enough mistake that it
 * should produce a sentence, not a blank page.
 */
let pool = null;
function getPool() {
  if (!configured) {
    throw new Error(
      'This console was built without VITE_USER_POOL_ID and '
      + 'VITE_USER_POOL_CLIENT_ID. Fill web/.env from the CDK stack outputs '
      + 'and rebuild.',
    );
  }
  if (!pool) pool = new CognitoUserPool({ UserPoolId: USER_POOL_ID, ClientId: CLIENT_ID });
  return pool;
}

let cachedUser = null;

/** Resolve the current ID token, refreshing silently if the session expired. */
export function idToken() {
  return new Promise((resolve, reject) => {
    const user = cachedUser || getPool().getCurrentUser();
    if (!user) return reject(new Error('not signed in'));
    user.getSession((err, session) => {
      if (err || !session?.isValid()) return reject(err || new Error('session expired'));
      cachedUser = user;
      resolve(session.getIdToken().getJwtToken());
    });
  });
}

export function currentEmail() {
  const user = cachedUser || getPool().getCurrentUser();
  return user?.getUsername() ?? null;
}

/**
 * Sign in. MFA is required on the pool, so the common path resolves with
 * `{ mfa: true, respond }` and the caller collects the TOTP code.
 */
export function signIn(email, password) {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: getPool() });
    user.authenticateUser(
      new AuthenticationDetails({ Username: email, Password: password }),
      {
        onSuccess: () => { cachedUser = user; resolve({ mfa: false }); },
        onFailure: reject,
        totpRequired: () => {
          resolve({
            mfa: true,
            respond: (code) => new Promise((res, rej) => {
              user.sendMFACode(code, {
                onSuccess: () => { cachedUser = user; res(); },
                onFailure: rej,
              }, 'SOFTWARE_TOKEN_MFA');
            }),
          });
        },
        newPasswordRequired: () => {
          reject(new Error(
            'This account needs its temporary password changed. ' +
            'Set a permanent one with: aws cognito-idp admin-set-user-password'
          ));
        },
      },
    );
  });
}

export function signOut() {
  (cachedUser || getPool().getCurrentUser())?.signOut();
  cachedUser = null;
  location.reload();
}

export async function isSignedIn() {
  try { await idToken(); return true; } catch { return false; }
}
