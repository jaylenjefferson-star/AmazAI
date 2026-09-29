"""A new tenant row requires a verified email. An existing row does not.

Auth0 access tokens for the API audience do not carry ``email_verified``
unless a post-login Action adds the namespaced claim. ``/userinfo`` is the
fallback. See docs/AUTH0_EMAIL_ACTION.md.
"""

import json

import pytest

from amazai import identity as I
from amazai import keys as K
from amazai.store import Store

import handlers.api as api
import handlers.ws_auth as ws_auth


def test_an_existing_user_row_keeps_working_when_unverified(store):
    existing = I.Principal(user_id=store.owner_id, email="owner@example.com", email_verified=True)
    I.ensure_user(store, existing)
    unverified = I.Principal(user_id=store.owner_id, email_verified=False)
    assert I.ensure_verified_email(store, unverified, "stale") is unverified


def _bind(monkeypatch, table, principal):
    monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))

    def principal_from_event(evt):
        return principal

    monkeypatch.setattr(api.identity, "principal_from_event", principal_from_event)


def _call(principal):
    event = {
        "requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": {}}}},
        "rawPath": "/settings",
        "headers": {"authorization": "Bearer test-token"},
        "body": None,
    }
    resp = api.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None


def test_the_api_returns_403_and_creates_no_row_for_an_unverified_new_subject(table, monkeypatch):
    principal = I.Principal(user_id="auth0|new", email_verified=False)
    _bind(monkeypatch, table, principal)
    status, body = _call(principal)
    assert status == 403
    assert body["code"] == "EMAIL_NOT_VERIFIED"
    assert body["error"] == "EMAIL_NOT_VERIFIED"
    assert Store("auth0|new", table=table).try_get(K.user_pk("auth0|new"), "META") is None


def test_the_api_creates_a_row_for_a_verified_new_subject(table, monkeypatch):
    principal = I.Principal(user_id="auth0|new", email="new@example.com", email_verified=True)
    _bind(monkeypatch, table, principal)
    status, body = _call(principal)
    assert status == 200
    row = Store("auth0|new", table=table).try_get(K.user_pk("auth0|new"), "META")
    assert row["userId"] == "auth0|new"
    assert body is not None


def test_the_api_still_serves_an_existing_unverified_subject(table, monkeypatch):
    store = Store("auth0|old", table=table)
    I.ensure_user(store, I.Principal(user_id="auth0|old", email_verified=True))
    principal = I.Principal(user_id="auth0|old", email_verified=False)
    _bind(monkeypatch, table, principal)
    status, _body = _call(principal)
    assert status == 200


def test_ws_connect_rejects_an_unverified_new_subject(table, monkeypatch):
    monkeypatch.setattr(ws_auth, "Store", lambda owner_id: Store(owner_id, table=table))
    monkeypatch.setattr(
        ws_auth.identity, "verify",
        lambda token: I.Principal(user_id="auth0|new", email_verified=False),
    )
    monkeypatch.setattr(ws_auth.identity, "assert_owner", lambda principal: None)
    with pytest.raises(Exception, match="Unauthorized"):
        ws_auth.handler({
            "queryStringParameters": {"token": "t"},
            "methodArn": "arn:aws:execute-api:us-west-2:1:ws/live/$connect",
        }, None)
    assert Store("auth0|new", table=table).try_get(K.user_pk("auth0|new"), "META") is None


def test_ws_connect_allows_an_existing_unverified_subject(table, monkeypatch):
    store = Store("auth0|old", table=table)
    I.ensure_user(store, I.Principal(user_id="auth0|old", email_verified=True))
    monkeypatch.setattr(ws_auth, "Store", lambda owner_id: Store(owner_id, table=table))
    monkeypatch.setattr(
        ws_auth.identity, "verify",
        lambda token: I.Principal(user_id="auth0|old", email_verified=False),
    )
    monkeypatch.setattr(ws_auth.identity, "assert_owner", lambda principal: None)
    result = ws_auth.handler({
        "queryStringParameters": {"token": "t"},
        "methodArn": "arn:aws:execute-api:us-west-2:1:ws/live/$connect",
    }, None)
    assert result["principalId"] == "auth0|old"
    assert result["context"]["sub"] == "auth0|old"
