"""Defensive parsing of the AgentCore invoke_harness event stream.

BUILD_PLAN Phase 3: "Parse tool-use events defensively -- check
contentBlockStart, toolUse, and contentBlockDelta, and tolerate `input`
arriving as a JSON string."

Tool-use arguments can arrive in three shapes across the stream: complete on
contentBlockStart, accumulated across partial_json deltas, or as an already-
encoded JSON string. All three are normalised to a dict here so the
orchestrator never has to care.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    TEXT = "text"
    TOOL_USE = "tool_use"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StreamEvent:
    kind: EventKind
    text: str = ""
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: dict = field(default_factory=dict)
    error: str = ""


def _coerce_input(raw) -> dict:
    """Tolerate a dict, a JSON string, empty, or malformed input."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {"_unparsed": raw}
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    if raw is None:
        return {}
    return {"_value": raw}


class StreamParser:
    """Feeds on raw stream events and yields normalised StreamEvents.

    Stateful because tool input may be accumulated across partial_json deltas
    keyed by content-block index.
    """

    def __init__(self) -> None:
        self._blocks: dict[int, dict] = {}

    def feed(self, event: dict) -> list[StreamEvent]:
        out: list[StreamEvent] = []

        if "runtimeClientError" in event:
            err = event["runtimeClientError"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return [StreamEvent(EventKind.ERROR, error=msg or "runtime client error")]

        if "contentBlockStart" in event:
            start = event["contentBlockStart"] or {}
            idx = start.get("contentBlockIndex", 0)
            tool = (start.get("start") or {}).get("toolUse") or start.get("toolUse")
            if tool:
                self._blocks[idx] = {
                    "name": tool.get("name", ""),
                    "id": tool.get("toolUseId") or tool.get("id", ""),
                    "buf": "",
                    "input": tool.get("input"),
                }
            return out

        if "contentBlockDelta" in event:
            block = event["contentBlockDelta"] or {}
            idx = block.get("contentBlockIndex", 0)
            delta = block.get("delta") or {}

            if "text" in delta and delta["text"]:
                out.append(StreamEvent(EventKind.TEXT, text=delta["text"]))

            tool_delta = delta.get("toolUse")
            if tool_delta:
                slot = self._blocks.setdefault(idx, {"name": "", "id": "", "buf": "", "input": None})
                if tool_delta.get("name"):
                    slot["name"] = tool_delta["name"]
                if tool_delta.get("toolUseId"):
                    slot["id"] = tool_delta["toolUseId"]
                if "input" in tool_delta and isinstance(tool_delta["input"], dict):
                    slot["input"] = tool_delta["input"]
                if tool_delta.get("partial_json"):
                    slot["buf"] += tool_delta["partial_json"]
            return out

        if "contentBlockStop" in event:
            idx = (event["contentBlockStop"] or {}).get("contentBlockIndex", 0)
            slot = self._blocks.pop(idx, None)
            if slot and slot["name"]:
                raw = slot["input"] if slot["input"] is not None else slot["buf"]
                out.append(StreamEvent(
                    EventKind.TOOL_USE,
                    tool_name=slot["name"],
                    tool_use_id=slot["id"],
                    tool_input=_coerce_input(raw),
                ))
            return out

        return [StreamEvent(EventKind.UNKNOWN)]

    def flush(self) -> list[StreamEvent]:
        """Emit any tool blocks left open when the stream ended."""
        out: list[StreamEvent] = []
        for slot in self._blocks.values():
            if slot["name"]:
                raw = slot["input"] if slot["input"] is not None else slot["buf"]
                out.append(StreamEvent(
                    EventKind.TOOL_USE,
                    tool_name=slot["name"],
                    tool_use_id=slot["id"],
                    tool_input=_coerce_input(raw),
                ))
        self._blocks.clear()
        return out
