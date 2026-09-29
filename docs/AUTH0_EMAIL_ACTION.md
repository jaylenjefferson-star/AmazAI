# Auth0 Action: email_verified on the access token

AmazAI's API verifies **access** tokens whose audience is
`https://api.amazai.co` (`config/auth0.json`). An Auth0 access token for a
custom API does not include the OIDC claims `email` or `email_verified`.
Those claims are on the ID token. Auth0 drops a non-namespaced copy if an
Action tries to set `email_verified` on the access token directly.

Until a post-login Action adds the namespaced claims below, the API falls
back to `GET https://<tenant>/userinfo` with the same bearer token and caches
a successful response for five minutes, keyed by the token. Auth0 often
rejects that call because the token's audience is the API, not the userinfo
endpoint. When neither the claim nor userinfo says the email is verified,
**no new user row is created**.

## What the code does

`identity.ensure_verified_email` runs in `handlers/api.py` before
`ensure_user`, and in `handlers/ws_auth.py` before `$connect` is allowed.

- A subject who already has a `USER#<sub>` / `META` row is admitted even if
  the token does not prove a verified email. Those rows existed before this
  gate. Refusing them would lock out current tenants, including the owner,
  until this Action is installed.
- Anyone else needs `email_verified` true. The HTTP API returns
  `403` with `code` and `error` set to `EMAIL_NOT_VERIFIED`. The console
  shows a verify-your-email screen for that code. `$connect` rejects the
  handshake; API Gateway does not pass a JSON body back to the browser, so
  the screen is driven by the HTTP response.
- `OWNER_EMAILS` / `OWNER_SUBJECTS` are unchanged and stay unset. The owner
  allowlist remains open.

Claim order on the access token:

1. `https://api.amazai.co/email_verified` and `https://api.amazai.co/email`
   when present.
2. Standard `email_verified` / `email` when present.
3. Otherwise `/userinfo`.

## Action to add in the Auth0 dashboard

Do this in the tenant `dev-msijboy7a85k3chd.us.auth0.com`. This repository
does not change the tenant.

Auth0 Dashboard → Actions → Library → Build Custom → Trigger: **Login /
Post Login**. Deploy it, then drag it into the Post Login flow.

```javascript
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://api.amazai.co';
  if (event.authorization) {
    api.accessToken.setCustomClaim(`${namespace}/email`, event.user.email);
    api.accessToken.setCustomClaim(
      `${namespace}/email_verified`,
      Boolean(event.user.email_verified),
    );
  }
};
```

The namespace must be an `https://` URL Auth0 does not own, and it must not
be the bare claim name `email_verified`. After the Action is live, a new
login (not a cached access token) carries the claims, and the API stops
depending on `/userinfo`.

Also confirm the database connection requires email verification before
first login, if that is the product policy. The API gate is the backstop
for a token that arrives unverified; it does not send the verification
message. Auth0 does.

## Dashboard to-do, separate from this Action

Allowed Callback URLs and Allowed Logout URLs on the SPA currently include
`https://amazai.co`, `http://localhost:5173`, and `http://localhost:4173`.
They do not include `https://www.amazai.co` (fine: hosting 301s it to the
apex) or `https://main.d2qtxrhp46u9pz.amplifyapp.com`. Login and logout on
the Amplify host fail until that origin is added as a callback URL, a logout
URL, and a web origin. No code change.
