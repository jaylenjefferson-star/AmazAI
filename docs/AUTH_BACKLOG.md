# Auth backlog

Not scheduled. No implementation in this change. Effort is scope, not a
calendar estimate.

## Dashboard to-do (no code)

A read-only look at the Auth0 tenant showed Allowed Callback URLs and
Allowed Logout URLs of `https://amazai.co`, `http://localhost:5173`, and
`http://localhost:4173`.

`https://www.amazai.co` is absent. That is fine: hosting 301s it to the apex
before the SPA runs, and the SPA's callback is `window.location.origin`.

`https://main.d2qtxrhp46u9pz.amplifyapp.com` is also absent. Login and logout
on that host fail. Add it to Allowed Callback URLs, Allowed Logout URLs, and
Allowed Web Origins if that host should keep working. CORS in
`infra/lib/amazai-stack.ts` already lists it. Do not treat this as a code
change.

## 1. Refresh tokens leave localStorage

`web/src/auth0.jsx` sets `cacheLocation="localstorage"` and
`useRefreshTokens`. The refresh token is then readable to any script that
runs in the origin. The comment in that file is why it is this way: Safari's
tracking prevention blocks Auth0's hidden-iframe session check, and a reload
has to keep the session. There is no idle timeout in the app. An Auth0
session lives until the refresh token expires or is revoked.

**Proposed fix.** Two layers, the first one in this repo and the second only
if XSS is the threat you are paying to close.

- In-memory cache plus rotation, in `web/src/auth0.jsx`: set
  `cacheLocation` to `memory`, keep rotating refresh tokens, and add an idle
  timer (no pointer or key events for a chosen window, then `startLogout`).
  A full reload signs the person in again through the Auth0 session cookie
  when that cookie is still present, and fails closed when Safari blocks the
  silent check. That is the behavior the current comment refused. Touch
  `auth0.jsx`, `EmailGate` / `AuthGate` only if the idle timer lives there,
  and the Auth0 SPA tests. No API change. The idle window itself is a
  product choice; Auth0's dashboard inactivity timeout can match it, and
  that half is a dashboard setting.
- Backend-for-frontend with an httpOnly cookie, if the refresh token must
  not be in the browser at all. A small token route on the API exchanges the
  Auth0 code and sets a `Secure`, `HttpOnly`, `SameSite` cookie. The SPA
  stops storing tokens. This is invasive because the console origin
  (`https://amazai.co`) and the API origin (API Gateway) are different
  sites: a cookie set by the API is not sent on AmazAI page loads unless
  something same-origin (Amplify rewrite or CloudFront) fronts both. That
  routing, plus CSRF on cookie-authenticated calls, is the whole project.
  The in-memory change does not require it.

## 2. WebSocket connect stops putting the access token in the query string

`web/src/ws.js` opens `${URL}?token=<access token>`. Browsers cannot set
`Authorization` on the handshake. The token can land in API Gateway access
logs, proxies, and anything else that records the URL. `ws_auth.py` does not
write it to DynamoDB.

**Proposed fix.** A single-use connect ticket, not the access token.

- `POST /ws/ticket` on the existing HTTP API, behind the JWT authorizer.
  The handler stores a random id under a short TTL (on the order of 30
  seconds) with the verified `sub`, then returns the id.
- The socket connects with `?ticket=` instead of `?token=`.
- `ws_auth.py` loads the ticket, deletes it, and admits that subject. A
  replay fails. The access token never appears on the socket URL.
- `web/src/ws.js` requests a ticket before `new WebSocket`. Tests cover
  issue, one-time use, and expiry.

That is one route, a Dynamo item with TTL (the table already has a `ttl`
attribute), the authorizer, and the client. No new AWS service.

`Sec-WebSocket-Protocol` is the smaller alternative: put the token in a
subprotocol and point the authorizer at that header. It keeps the token out
of the URL, and it is a change to `ws.js` plus the authorizer identity
source. It still sends the long-lived access token on every handshake, and
header logging captures it. The ticket is the fix to build. The header is
only a step if the ticket route is blocked.
