"""Pipedream Connect client.

Pipedream is a connector and an OAuth provider. It is not a second
enforcement layer, and nothing here decides what an agent may do — that
decision was already made in `policy.py` and `router.py` before a call
reaches this module.

The reason Connect fits AmazAI at all is the proxy. A call goes to Pipedream
with an account id, and Pipedream injects the end user's credentials
server-side before forwarding it upstream. The access token for the
third-party app is never held by this process, so it cannot be logged,
cannot be written to a store row, and cannot reach model context — not
because we are careful with it, but because we never have it.

Two invariants are enforced here rather than left to reviewers:

- `include_credentials` is never sent when listing accounts. The parameter
  exists in the API and would return the very material this design avoids
  holding.
- The upstream URL is checked against the catalog entry's allowed prefix
  before the call is made. A connector granted for chat.postMessage cannot be
  pointed at admin.users.delete by a model that controls the arguments.

API shapes are from https://pipedream.com/docs/connect/api-reference and the
proxy docs; do not improvise them.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from amazai import secrets

API_BASE = "https://api.pipedream.com/v1"
TOKEN_URL = f"{API_BASE}/oauth/token"

#: Access tokens last an hour; refresh early so a call never races expiry.
TOKEN_SKEW_SECONDS = 300

DEFAULT_TIMEOUT = 20


class PipedreamError(RuntimeError):
    """A call to Pipedream failed. Carries no credential material."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TargetNotAllowed(PermissionError):
    """The requested upstream URL is outside what the catalog entry permits.

    Distinct from a failed call: this one never left the process.
    """


def project_id() -> str:
    pid = os.environ.get("PIPEDREAM_PROJECT_ID", "")
    if not pid:
        raise PipedreamError("PIPEDREAM_PROJECT_ID is not configured")
    return pid


def environment() -> str:
    """Pipedream's own environment switch, separate from ours.

    Defaults to development so a misconfigured stack talks to test accounts
    rather than real ones.
    """
    return os.environ.get("PIPEDREAM_ENVIRONMENT", "development")


def encode_target(url: str) -> str:
    """URL-safe base64, unpadded — the form the proxy path expects."""
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _request(method: str, url: str, *, headers: dict, body: dict | None = None,
             timeout: int = DEFAULT_TIMEOUT) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"raw": raw}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:500]
        # The body is echoed because it names the upstream problem; it is the
        # third party's error text, not ours, and carries no local secret.
        raise PipedreamError(f"{method} failed ({exc.code}): {detail}",
                             status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise PipedreamError(f"{method} could not reach Pipedream: {exc.reason}") from exc


class Pipedream:
    """One client per Lambda container.

    The access token is cached in memory and refreshed before expiry. It is a
    Pipedream token, not a third-party one, and still never leaves this
    object.
    """

    def __init__(self, *, credentials: dict | None = None,
                 request=_request) -> None:
        self._credentials = credentials
        self._request = request
        self._token: str | None = None
        self._expires_at: float = 0.0

    # -- auth ---------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - TOKEN_SKEW_SECONDS:
            return self._token

        creds = self._credentials or secrets.pipedream_credentials()
        _, payload = self._request("POST", TOKEN_URL, headers={
            "content-type": "application/json",
        }, body={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        })

        token = payload.get("access_token")
        if not token:
            raise PipedreamError("Pipedream returned no access_token")
        self._token = token
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        return token

    def _headers(self, *, json_body: bool = True) -> dict:
        headers = {
            "authorization": f"Bearer {self._access_token()}",
            "x-pd-environment": environment(),
        }
        if json_body:
            headers["content-type"] = "application/json"
        return headers

    def _project_url(self, path: str) -> str:
        return f"{API_BASE}/connect/{project_id()}{path}"

    # -- accounts -----------------------------------------------------------

    def connect_token(self, external_user_id: str) -> dict:
        """Mint a short-lived token for the Connect Link authorization flow.

        The owner authorizes the third party in Pipedream's UI; the resulting
        credential lives on Pipedream's side and is referenced here only by
        account id.
        """
        _, payload = self._request("POST", self._project_url("/tokens"),
                                   headers=self._headers(),
                                   body={"external_user_id": external_user_id})
        return payload

    def accounts(self, external_user_id: str, *, app: str | None = None) -> list[dict]:
        """Connected accounts for this owner.

        `include_credentials` is deliberately never sent. It would return the
        third-party access token, which is precisely the material this design
        exists to never hold.
        """
        params = {"external_user_id": external_user_id}
        if app:
            params["app"] = app
        url = self._project_url("/accounts") + "?" + urllib.parse.urlencode(params)
        _, payload = self._request("GET", url, headers=self._headers(json_body=False))

        out = []
        for row in payload.get("data", []):
            out.append({
                "accountId": row.get("id"),
                "app": (row.get("app") or {}).get("name_slug"),
                "appName": (row.get("app") or {}).get("name"),
                "name": row.get("name"),
                "healthy": bool(row.get("healthy")),
                "dead": bool(row.get("dead")),
                "scopes": row.get("authorized_scopes") or [],
                "createdAt": row.get("created_at"),
            })
        return out

    # -- invocation ---------------------------------------------------------

    def proxy(self, *, external_user_id: str, account_id: str, target_url: str,
              method: str = "POST", body: dict | None = None,
              allowed_prefixes: tuple[str, ...] = ()) -> dict:
        """Call an upstream API as the connected account.

        `allowed_prefixes` comes from the catalog entry, not from the caller's
        arguments, and is checked here as well as at resolution time. The
        model controls the arguments to a tool; it must not be able to steer a
        granted connector at an endpoint the grant never covered.
        """
        if allowed_prefixes and not target_url.startswith(allowed_prefixes):
            raise TargetNotAllowed(
                f"{target_url} is outside the allowed targets for this connector"
            )

        params = {"external_user_id": external_user_id, "account_id": account_id}
        url = (self._project_url(f"/proxy/{encode_target(target_url)}")
               + "?" + urllib.parse.urlencode(params))

        headers = self._headers()
        # The proxy is always POSTed to; the upstream verb rides along.
        headers["x-pd-proxy-method"] = method.upper()

        _, payload = self._request("POST", url, headers=headers, body=body or {})
        return payload
