"""Auth0 request authorizer for the WebSocket ``$connect`` route.

Browsers cannot attach an Authorization header to the WebSocket handshake, so
the console supplies a short-lived Auth0 access token as the ``token`` query
parameter. API Gateway invokes this function only for ``$connect``; the
verified subject is returned as authorizer context and the socket handler uses
that value for every later frame.

The token is deliberately not written to DynamoDB, logs, or authorizer
context. The connection's identity is the verified Auth0 subject, nothing
else.
"""

from __future__ import annotations

from amazai import identity
from amazai.store import Store


def handler(event, context):  # noqa: ARG001
    token = ((event.get("queryStringParameters") or {}).get("token") or "").strip()
    try:
        principal = identity.verify(token)
        identity.assert_owner(principal)
        # Same rule as the HTTP handler: no USER# row is created here, and a
        # new subject with an unverified email is refused. API Gateway turns
        # this into a rejected handshake, not a JSON body; the HTTP route is
        # what returns 403 EMAIL_NOT_VERIFIED to the console.
        store = Store(principal.user_id)
        identity.ensure_verified_email(store, principal, token)
    except (identity.AuthError, identity.EmailNotVerified):
        # Raising Unauthorized makes API Gateway reject the handshake without
        # exposing why a supplied token was not accepted.
        raise Exception("Unauthorized") from None

    return {
        "principalId": principal.user_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": "Allow",
                "Resource": event["methodArn"],
            }],
        },
        "context": {"sub": principal.user_id},
    }
