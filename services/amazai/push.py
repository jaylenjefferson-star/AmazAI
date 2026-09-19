"""WebSocket push to the console.

`post_to_connection` is the only way the orchestrator reaches your screen.
Routines firing while you are away push into the same socket, so the console
updates itself.

A dead connection is normal (closed laptop) and must never fail a run: stale
connections are pruned and the run continues.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3

WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")


class Push:
    def __init__(self, store, *, endpoint: str | None = None, client=None) -> None:
        self._store = store
        endpoint = endpoint or WS_ENDPOINT
        self._client = client
        if self._client is None and endpoint:
            self._client = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)

    def _connections(self) -> list[str]:
        rows = self._store.query_index("gsi1", "gsi1pk", "CONNS", limit=50)
        return [r["connectionId"] for r in rows if r.get("connectionId")]

    def send(self, payload: dict[str, Any]) -> int:
        """Fan out to every live console connection. Returns the delivered count."""
        if not self._client:
            return 0
        from amazai import keys as K

        body = json.dumps(payload, default=str).encode()
        delivered = 0
        for conn in self._connections():
            try:
                self._client.post_to_connection(ConnectionId=conn, Data=body)
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                if "GoneException" in type(exc).__name__ or "410" in str(exc):
                    # Closed laptop. Prune and carry on -- never fail a run
                    # because nobody is watching.
                    try:
                        self._store.delete(K.connection_pk(conn), "META")
                    except Exception:  # noqa: BLE001, S110
                        pass
                # Any other push failure is also non-fatal by design.
        return delivered

    # -- typed events -------------------------------------------------------

    def delta(self, run_id: str, thread_id: str, text: str) -> None:
        self.send({"type": "delta", "runId": run_id, "threadId": thread_id, "text": text})

    def tool(self, run_id: str, thread_id: str, name: str, summary: str = "") -> None:
        self.send({"type": "tool", "runId": run_id, "threadId": thread_id,
                   "name": name, "summary": summary})

    def state(self, run_id: str, thread_id: str, state: str, cost_usd: float = 0.0) -> None:
        self.send({"type": "run.state", "runId": run_id, "threadId": thread_id,
                   "state": state, "costUsd": cost_usd})

    def approval_requested(self, run_id: str, thread_id: str, approval: dict) -> None:
        self.send({"type": "approval.requested", "runId": run_id,
                   "threadId": thread_id, "approval": approval})

    def run_end(self, run_id: str, thread_id: str, state: str, summary: str,
                cost_usd: float) -> None:
        self.send({"type": "run.end", "runId": run_id, "threadId": thread_id,
                   "state": state, "summary": summary, "costUsd": cost_usd})

    def notification(self, level: str, message: str, **extra) -> None:
        self.send({"type": "notification", "level": level, "message": message, **extra})
