"""Composio client.

Composio is a way to reach an app on a person's behalf, and a place for their
OAuth tokens to live. It is not a second enforcement layer: nothing here decides
what a Bot may do. That was already decided in `policy.py` and `connectors.py`
before a call reaches this module, and this module never returns a credential.

**What is deliberately not used.** A Composio *session* hands an agent its own
meta tools -- an execute-anything tool, a connection manager, a remote bash
sandbox. Passed straight through, the model would run Composio actions without
ever meeting AmazAI's approval gate. So sessions are used for exactly one thing,
minting a Connect Link, and tools are executed one at a time through the direct
endpoint, by our code, after the gate has said yes.

**What classifies a tool.** Composio tags every tool with at least one of
`readOnlyHint`, `createHint`, `updateHint`, `destructiveHint`, and documents
those four as the ones to use for access control. `classify` maps them to a
`Capability` and fails closed: a tool with no recognised tag is a write, not a
read, so an unlabelled tool asks for approval instead of slipping through.

API shapes are from https://docs.composio.dev/reference (REST v3.1, base URL
below). Do not improvise them; the version is named on purpose, because the
previous one (`/api/v3`) pins old tool versions and lacks the current tags.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from amazai import secrets
from amazai.policy import Capability

API_BASE = "https://backend.composio.dev/api/v3.1"
DEFAULT_TIMEOUT = 25

#: What a tool result may weigh before it is cut down. It goes into model
#: context, and a whole mailbox in one turn is neither useful nor safe.
MAX_RESULT_CHARS = 20_000

#: Tool and toolkit slugs are interpolated into URL paths. Anything outside this
#: alphabet never reaches a request, whatever a model wrote.
_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ComposioError(RuntimeError):
    """A call to Composio failed. Carries no credential material.

    `log_id` is Composio's own execution log id when it returned one, which is
    what to quote when asking why a call failed.
    """

    def __init__(self, message: str, *, status: int | None = None,
                 log_id: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.log_id = log_id


def valid_slug(slug: object) -> bool:
    return isinstance(slug, str) and bool(_SLUG.match(slug))


def _require_slug(slug: object, what: str) -> str:
    if not valid_slug(slug):
        raise ComposioError(f"{what} {str(slug)[:40]!r} is not a valid slug")
    return slug  # type: ignore[return-value]


# --- classification --------------------------------------------------------

_WRITE_TAGS = frozenset({"createHint", "updateHint"})


def classify(tags: object) -> Capability:
    """Turn a Composio tool's behaviour tags into what AmazAI already understands.

    A tool that is tagged read-only *and* anything else is not a read. A tool
    with no tag at all is a write. Both fall the safe way: into the approval
    gate.
    """
    have = {str(t) for t in tags} if isinstance(tags, (list, tuple, set, frozenset)) else set()
    if "destructiveHint" in have:
        return Capability.DESTRUCTIVE
    if have & _WRITE_TAGS:
        return Capability.WRITE
    if "readOnlyHint" in have:
        return Capability.READ
    return Capability.WRITE


# --- transport -------------------------------------------------------------

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
        raw = exc.read().decode()[:2000]
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        raise ComposioError(_describe(exc.code, payload), status=exc.code,
                            log_id=_log_id(payload)) from exc
    except urllib.error.URLError as exc:
        raise ComposioError(f"{method} could not reach Composio: {exc.reason}") from exc


def _log_id(payload: object) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        for src in (payload, err if isinstance(err, dict) else {}):
            for key in ("log_id", "logId", "request_id", "requestId"):
                if isinstance(src.get(key), str):
                    return src[key]
    return ""


def _describe(status: int, payload: object) -> str:
    message = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        message = (err.get("message") if isinstance(err, dict) else err) or payload.get("message") or ""
    if status in (401, 403):
        # The project key is missing, wrong, or for another project. Said plainly
        # and without echoing anything: this text reaches the console.
        return f"Composio rejected the project key ({status}). Check the amazai/composio secret."
    return f"Composio returned {status}: {str(message)[:300] or 'no detail'}"


def _clip(data: object) -> object:
    """Cap a tool result before it becomes model context."""
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        text = str(data)
    if len(text) <= MAX_RESULT_CHARS:
        return data
    return {"truncated": True, "note": f"result was {len(text)} characters; showing the start",
            "preview": text[:MAX_RESULT_CHARS]}


def _names(items: object) -> list[str]:
    out: list[str] = []
    for c in items or []:
        name = (c.get("name") or c.get("slug")) if isinstance(c, dict) else c
        if isinstance(name, str):
            out.append(name)
    return out


class Composio:
    """One client per Lambda container.

    The project key comes from Secrets Manager on first use and is held only in
    this object. It goes into one header on requests to one host; it is not put
    in an error, a log line, or anything a model can read.
    """

    def __init__(self, *, api_key: str | None = None, request=_request) -> None:
        self._api_key = api_key
        self._request = request

    def _headers(self) -> dict:
        key = self._api_key or secrets.composio_api_key()
        return {"x-api-key": key, "content-type": "application/json",
                "accept": "application/json"}

    def _call(self, method: str, path: str, *, params: dict | None = None,
              body: dict | None = None) -> dict:
        url = f"{API_BASE}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "", [])}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        _, payload = self._request(method, url, headers=self._headers(), body=body)
        return payload if isinstance(payload, dict) else {"raw": payload}

    # -- apps -----------------------------------------------------------------

    @staticmethod
    def _app(row: dict) -> dict:
        meta = row.get("meta") or {}
        return {
            "slug": row.get("slug", ""),
            "name": row.get("name") or row.get("slug", ""),
            "description": meta.get("description", ""),
            "logo": meta.get("logo", ""),
            "categories": _names(meta.get("categories")),
            "toolsCount": meta.get("tools_count"),
            "noAuth": bool(row.get("no_auth")),
        }

    def toolkits(self, *, search: str | None = None, cursor: str | None = None,
                 limit: int = 48) -> dict:
        """Every app Composio offers, paged. No allowlist: that is the point."""
        payload = self._call("GET", "/toolkits", params={
            "search": search, "cursor": cursor, "limit": max(1, min(limit, 100)),
            "sort_by": "usage"})
        return {"apps": [self._app(r) for r in payload.get("items", [])],
                "pageInfo": {"end_cursor": payload.get("next_cursor") or ""},
                "total": payload.get("total_items")}

    def toolkit(self, slug: str) -> dict:
        slug = _require_slug(slug, "toolkit")
        return self._app(self._call("GET", f"/toolkits/{urllib.parse.quote(slug, safe='')}"))

    # -- connections ----------------------------------------------------------

    def accounts(self, user_id: str, *, toolkit: str | None = None,
                 statuses: tuple[str, ...] = ("ACTIVE",)) -> list[dict]:
        """A person's connected accounts. References only: never a token."""
        payload = self._call("GET", "/connected_accounts", params={
            "user_ids": [user_id],
            "toolkit_slugs": [toolkit] if toolkit else None,
            "statuses": list(statuses) or None, "limit": 100})
        out = []
        for row in payload.get("items", []):
            out.append({"id": row.get("id", ""),
                        "app": (row.get("toolkit") or {}).get("slug", ""),
                        "status": row.get("status", ""),
                        "alias": row.get("alias") or ""})
        return out

    def connect_link(self, user_id: str, toolkit: str, *,
                     callback_url: str | None = None) -> dict:
        """A hosted sign-in page for one app. The credential never reaches us.

        Composio's session is only the vehicle here: it is created with its code
        sandbox and connection tools switched off, and used for nothing but the
        link. It has no tools a model can be handed.
        """
        toolkit = _require_slug(toolkit, "toolkit")
        session = self._call("POST", "/tool_router/session", body={
            "user_id": user_id,
            "toolkits": {"enable": [toolkit]},
            "workbench": {"enable": False},
            "manage_connections": {"enable": False},
        })
        session_id = _require_slug(session.get("session_id"), "session")
        link_body: dict = {"toolkit": toolkit}
        if callback_url:
            link_body["callback_url"] = callback_url
        link = self._call("POST", f"/tool_router/session/{session_id}/link", body=link_body)
        url = link.get("redirect_url")
        if not url:
            raise ComposioError("Composio returned no Connect Link")
        return {"connectLinkUrl": url, "accountId": link.get("connected_account_id", "")}

    # -- tools ----------------------------------------------------------------

    @staticmethod
    def _tool(row: dict) -> dict:
        tags = row.get("tags") or []
        return {
            "tool": row.get("slug", ""),
            "name": row.get("name", ""),
            "toolkit": (row.get("toolkit") or {}).get("slug", ""),
            "description": (row.get("human_description") or row.get("description") or ""),
            "capability": classify(tags).value,
            "tags": [str(t) for t in tags],
            "inputSchema": row.get("input_parameters") or {},
        }

    def tools(self, toolkit: str, *, query: str | None = None, limit: int = 10,
              cursor: str | None = None) -> list[dict]:
        """Tools in one toolkit. Newest versions, because the tags a gate reads
        are only on those."""
        toolkit = _require_slug(toolkit, "toolkit")
        payload = self._call("GET", "/tools", params={
            "toolkit_slug": toolkit, "query": query, "cursor": cursor,
            "limit": max(1, min(limit, 50)), "toolkit_versions": "latest"})
        return [self._tool(r) for r in payload.get("items", [])
                if not r.get("is_deprecated")]

    def tool(self, slug: str) -> dict:
        slug = _require_slug(slug, "tool")
        return self._tool(self._call(
            "GET", f"/tools/{urllib.parse.quote(slug, safe='')}",
            params={"toolkit_versions": "latest"}))

    def execute(self, user_id: str, slug: str, arguments: dict, *,
                account_id: str | None = None) -> dict:
        """Run one tool as one person. Raises on any failure, with the log id."""
        slug = _require_slug(slug, "tool")
        body: dict = {"user_id": user_id, "arguments": arguments or {}}
        if account_id:
            body["connected_account_id"] = account_id
        payload = self._call("POST", f"/tools/execute/{urllib.parse.quote(slug, safe='')}",
                             body=body)
        log_id = payload.get("log_id") or ""
        if payload.get("successful") is False or payload.get("error"):
            raise ComposioError(f"{slug} failed: {str(payload.get('error'))[:300]}",
                                log_id=log_id)
        return {"data": _clip(payload.get("data")), "logId": log_id}
