"""A live check of a Composio project key, safe to run against a real account.

Composio's own bar for "the integration works" is a real, read-only tool call
that returns a provider result and a log id. This runs exactly that, and only
that: it lists apps (proves the key), optionally lists one person's connections,
and optionally executes one tool -- but only a tool Composio labels read-only. A
write is refused here, before any request, and never printed as a result.

Nothing is written anywhere, no account is created, and neither the key nor any
value in a returned result is printed -- only names, counts and the log id.
"""

from __future__ import annotations

from amazai import composio as cp
from amazai.policy import Capability


def run_check(client: cp.Composio, *, user_id: str = "", tool: str = "",
              arguments: dict | None = None) -> tuple[int, list[str]]:
    """Returns (exit code, lines to print). 0 ok, 1 a call failed, 2 refused."""
    out: list[str] = []
    try:
        page = client.toolkits(limit=1)
    except cp.ComposioError as exc:
        return 1, [f"FAIL  the key was not accepted or Composio is unreachable: {exc}"]
    out.append(f"ok    key accepted; Composio offers {page.get('total') or 'many'} apps")

    if user_id:
        try:
            accounts = client.accounts(user_id)
        except cp.ComposioError as exc:
            return 1, out + [f"FAIL  could not list connections: {exc}"]
        apps = sorted({a["app"] for a in accounts})
        out.append(f"ok    {len(accounts)} active connection(s) for that user: "
                   f"{', '.join(apps) or 'none yet'}")

    if tool:
        if not user_id:
            return 2, out + ["REFUSED  --tool needs --user-id: a tool runs as one person"]
        try:
            meta = client.tool(tool)
        except cp.ComposioError as exc:
            return 1, out + [f"FAIL  no such tool: {exc}"]
        if Capability(meta["capability"]) is not Capability.READ:
            return 2, out + [f"REFUSED  {tool} is {meta['capability']}, not read-only "
                             f"(tags: {', '.join(meta['tags']) or 'none'}). This check "
                             "never runs anything that could change data."]
        try:
            result = client.execute(user_id, tool, arguments or {})
        except cp.ComposioError as exc:
            log = f" (log {exc.log_id})" if exc.log_id else ""
            return 1, out + [f"FAIL  {tool}: {exc}{log}"]
        data = result["data"]
        shape = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        out.append(f"ok    {tool} ran as a read; log id {result['logId'] or '(none returned)'}; "
                   f"result fields: {shape}")
    return 0, out
