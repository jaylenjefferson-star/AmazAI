"""A scripted Composio, one layer below the client.

`amazai.composio.Composio` takes its HTTP function as an argument. These tests
hand it `FakeTransport`, so the real client -- URL building, query encoding,
response parsing, classification, error handling -- runs unmodified against
responses in Composio's documented v3.1 shapes. Only the network hop is faked.

The app and tool names below are test data. They are not a claim about what
Composio's catalog contains; what is real is the *shape* (slug, toolkit, tags,
input_parameters) and the four behaviour tags, which come from the docs.
"""

from __future__ import annotations

import json
import urllib.parse

from amazai import composio as cp

READ = "SLACK_FETCH_CONVERSATION_HISTORY"
WRITE = "SLACK_SEND_MESSAGE"
DELETE = "SLACK_DELETE_A_MESSAGE"
UNLABELLED = "SLACK_A_TOOL_WITH_NO_TAGS"
GMAIL_READ = "GMAIL_FETCH_EMAILS"
GMAIL_SEND = "GMAIL_SEND_EMAIL"


def _tool(slug: str, toolkit: str, tags: list[str], props: dict, required: list[str],
          description: str = "") -> dict:
    return {
        "slug": slug, "name": slug.replace("_", " ").title(),
        "description": description or f"{slug} does one thing in {toolkit}.",
        "toolkit": {"slug": toolkit, "name": toolkit.title(), "logo": ""},
        "input_parameters": {"type": "object", "properties": props, "required": required},
        "tags": tags, "is_deprecated": False, "no_auth": False,
    }


TOOLS = [
    _tool(READ, "slack", ["readOnlyHint"],
          {"channel": {"type": "string", "description": "Channel id"}, "limit": {"type": "integer"}},
          ["channel"]),
    _tool(WRITE, "slack", ["createHint"],
          {"channel": {"type": "string"}, "text": {"type": "string"}}, ["channel", "text"]),
    _tool(DELETE, "slack", ["destructiveHint", "updateHint"],
          {"channel": {"type": "string"}, "ts": {"type": "string"}}, ["channel", "ts"]),
    _tool(UNLABELLED, "slack", [], {"x": {"type": "string"}}, []),
    _tool(GMAIL_READ, "gmail", ["readOnlyHint"], {"query": {"type": "string"}}, []),
    _tool(GMAIL_SEND, "gmail", ["createHint"],
          {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
          ["to", "body"]),
]

TOOLKITS = {
    "slack": {"slug": "slack", "name": "Slack", "no_auth": False,
              "meta": {"description": "Team chat.", "logo": "", "tools_count": 4,
                       "categories": [{"name": "Communication"}]}},
    "gmail": {"slug": "gmail", "name": "Gmail", "no_auth": False,
              "meta": {"description": "Email.", "logo": "", "tools_count": 2,
                       "categories": [{"name": "Email"}]}},
    "github": {"slug": "github", "name": "GitHub", "no_auth": False,
               "meta": {"description": "Code hosting.", "logo": "", "tools_count": 0,
                        "categories": []}},
}


class FakeTransport:
    """Records every request; answers in Composio's shapes.

    `connected` is the set of toolkit slugs the user has an ACTIVE account for.
    `fail` maps a tool slug to the error text its execution returns.
    `huge` is a tool slug whose result is far larger than a model should get.
    """

    def __init__(self, connected: set[str] | None = None, *, fail: dict | None = None,
                 huge: str | None = None) -> None:
        self.connected = set(connected if connected is not None else {"slack"})
        self.fail = fail or {}
        self.huge = huge
        self.requests: list[dict] = []

    # -- what tests assert on ------------------------------------------------

    @property
    def executed(self) -> list[dict]:
        return [r for r in self.requests if r["path"].startswith("/tools/execute/")]

    # -- the transport ---------------------------------------------------------

    def __call__(self, method, url, *, headers, body=None, timeout=25):  # noqa: ARG002
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.removeprefix("/api/v3.1")
        query = urllib.parse.parse_qs(parsed.query)
        self.requests.append({"method": method, "path": path, "query": query,
                              "body": body, "headers": dict(headers), "url": url})

        if method == "GET" and path == "/toolkits":
            term = (query.get("search") or [""])[0].lower()
            rows = [t for t in TOOLKITS.values() if term in t["name"].lower() or term in t["slug"]]
            return 200, {"items": rows, "next_cursor": None, "total_items": len(rows)}

        if method == "GET" and path.startswith("/toolkits/"):
            slug = path.rsplit("/", 1)[-1]
            if slug not in TOOLKITS:
                raise cp.ComposioError("Composio returned 404: toolkit not found", status=404)
            return 200, TOOLKITS[slug]

        if method == "GET" and path == "/connected_accounts":
            wanted = query.get("toolkit_slugs")
            rows = [{"id": f"ca_{s}", "status": "ACTIVE", "alias": None,
                     "toolkit": {"slug": s}} for s in sorted(self.connected)
                    if not wanted or s in wanted]
            return 200, {"items": rows}

        if method == "POST" and path == "/tool_router/session":
            return 201, {"session_id": "trs_test", "tool_router_tools": []}

        if method == "POST" and path == "/tool_router/session/trs_test/link":
            return 201, {"link_token": "lt_x", "connected_account_id": f"ca_{body['toolkit']}",
                         "redirect_url": f"https://connect.composio.dev/link/{body['toolkit']}"}

        if method == "GET" and path == "/tools":
            toolkit = (query.get("toolkit_slug") or [""])[0]
            term = (query.get("query") or [""])[0].lower()
            rows = [t for t in TOOLS if t["toolkit"]["slug"] == toolkit
                    and (not term or term in t["slug"].lower() or term in t["description"].lower()
                         or term in t["name"].lower())]
            return 200, {"items": rows, "next_cursor": None}

        if method == "GET" and path.startswith("/tools/"):
            slug = path.rsplit("/", 1)[-1]
            row = next((t for t in TOOLS if t["slug"] == slug), None)
            if row is None:
                raise cp.ComposioError("Composio returned 404: tool not found", status=404)
            return 200, row

        if method == "POST" and path.startswith("/tools/execute/"):
            slug = path.rsplit("/", 1)[-1]
            if slug in self.fail:
                return 200, {"data": {}, "error": self.fail[slug], "successful": False,
                             "log_id": "log_failed"}
            data = {"ok": True, "echo": body.get("arguments")}
            if slug == self.huge:
                data = {"rows": ["x" * 100] * 1000}
            return 200, {"data": data, "error": None, "successful": True, "log_id": "log_ok"}

        raise AssertionError(f"unscripted request: {method} {path}")


def client(transport: FakeTransport | None = None) -> tuple[cp.Composio, FakeTransport]:
    transport = transport or FakeTransport()
    return cp.Composio(api_key="ak_test_key_not_real", request=transport), transport


def wire(monkeypatch, transport: FakeTransport | None = None):
    """Point both the API and the orchestrator at one fake, and return it."""
    import handlers.api as api
    import handlers.orchestrator as orch
    c, t = client(transport)
    monkeypatch.setattr(api, "_composio", lambda: c)
    monkeypatch.setattr(orch, "_composio_client", lambda: c)
    return c, t


def body_of(request: dict) -> str:
    return json.dumps(request["body"], default=str)
