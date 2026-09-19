import {
  CognitoUser,
  CognitoUserPool,
  AuthenticationDetails,
} from 'amazon-cognito-identity-js';

const pool = new CognitoUserPool({
  UserPoolId: import.meta.env.VITE_USER_POOL_ID,
  ClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID,
});

let cachedUser = null;

/** Resolve the current ID token, refreshing silently if the session expired. */
export function idToken() {
  return new Promise((resolve, reject) => {
    const user = cachedUser || pool.getCurrentUser();
    if (!user) return reject(new Error('not signed in'));
    user.getSession((err, session) => {
      if (err || !session?.isValid()) return reject(err || new Error('session expired'));
      cachedUser = user;
      resolve(session.getIdToken().getJwtToken());
    });
  });
}

export function currentEmail() {
  const user = cachedUser || pool.getCurrentUser();
  return user?.getUsername() ?? null;
}

/**
 * Sign in. MFA is required on the pool, so the common path resolves with
 * `{ mfa: true, respond }` and the caller collects the TOTP code.
 */
export function signIn(email, password) {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: pool });
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
  (cachedUser || pool.getCurrentUser())?.signOut();
  cachedUser = null;
  location.reload();
}

export async function isSignedIn() {
  try { await idToken(); return true; } catch { return false; }
}
