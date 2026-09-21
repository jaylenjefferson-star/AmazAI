"""Defensive parsing of the AgentCore invoke_harness event stream.

BUILD_PLAN Phase 3: "Parse tool-use events defensively -- check
contentBlockStart, toolUse, and contentBlockDelta, and tolerate `input`
arriving as a JSON string."

Tool-use arguments arrive from current AgentCore as JSON-string fragments under
`contentBlockDelta.delta.toolUse.input`; older previews and test doubles have
also put a complete document on `contentBlockStart`, a complete dict on a
delta, or fragments under `partial_json`. All accepted shapes are accumulated
and normalised to a dict here so the orchestrator never has to care. An empty
start placeholder never overrides fragments that arrive later.

The same defensiveness applies to the token counts. A turn's usage arrives
once per model call, in a trailing `metadata` event, and it is the only place
the provider says what the call cost. Miss it and every downstream number --
the run's cost, the month's spend, every budget ceiling -- is structurally
zero. So the field names are matched liberally (camelCase and snake_case,
`inputTokens` and `promptTokens`) and the values are coerced, because an
unrecognised spelling here fails silently and looks like a free product.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    TEXT = "text"
    TOOL_USE = "tool_use"
    USAGE = "usage"
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
    #: Set on USAGE events only. `cached` and `reasoning` stay None when the
    #: provider did not report them, so "not reported" and "reported as none"
    #: remain distinguishable -- the same rule `usage.TokenCounts` follows.
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int | None = None
    #: Structural diagnostics only: no argument values. A named tool with
    #: required input and `False` here means a provider event shape was missed
    #: or the stream ended before any input arrived.
    tool_input_observed: bool = False
    block_index: int = -1


#: Spellings seen across providers and SDK previews for the same four counts.
#: Longest-standing first; the first key present wins.
_TOKEN_FIELDS = {
    "input": ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens"),
    "output": ("outputTokens", "output_tokens", "completionTokens", "completion_tokens"),
    "cached": ("cacheReadInputTokens", "cachedTokens", "cache_read_input_tokens",
               "cached_tokens", "cacheReadTokens"),
    "reasoning": ("reasoningTokens", "reasoning_tokens", "thinkingTokens",
                  "thinking_tokens"),
}


def _int(raw) -> int | None:
    """A count, or None when it is absent or unusable.

    Providers have sent these as ints, as decimal strings, and as floats.
    None is returned rather than 0 so an absent count cannot be mistaken for
    a reported zero.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(float(raw.strip()))
        except (ValueError, TypeError):
            return None
    return None


def _pick(block: dict, names: tuple[str, ...]) -> int | None:
    for name in names:
        if name in block:
            value = _int(block[name])
            if value is not None:
                return value
    return None


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


def _new_tool_slot(tool: dict | None = None) -> dict:
    """State for one block, preserving strings as accumulable fragments."""
    tool = tool or {}
    raw = tool.get("input")
    return {
        "name": tool.get("name", ""),
        "id": tool.get("toolUseId") or tool.get("id", ""),
        "buf": raw if isinstance(raw, str) else "",
        "input": None if isinstance(raw, str) else raw,
        "observed": "input" in tool,
    }


def _meaningful(raw) -> bool:
    if raw is None:
        return False
    if isinstance(raw, str):
        return bool(raw.strip())
    if isinstance(raw, (dict, list)):
        return bool(raw)
    return True


def _raw_tool_input(slot: dict):
    """Prefer a complete non-empty document, then accumulated JSON text.

    Some streams put `{}` or `""` on start as a placeholder and send the real
    JSON in deltas. Treating any non-None start value as authoritative silently
    discarded those later fragments.
    """
    explicit = slot.get("input")
    buffered = slot.get("buf", "")
    if _meaningful(explicit):
        return explicit
    if _meaningful(buffered):
        return buffered
    return explicit if explicit is not None else buffered


def _tool_event(slot: dict, index: int) -> StreamEvent:
    return StreamEvent(
        EventKind.TOOL_USE,
        tool_name=slot.get("name", ""),
        tool_use_id=slot.get("id", ""),
        tool_input=_coerce_input(_raw_tool_input(slot)),
        tool_input_observed=bool(slot.get("observed")),
        block_index=index,
    )


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
                self._blocks[idx] = _new_tool_slot(tool)
            return out

        if "contentBlockDelta" in event:
            block = event["contentBlockDelta"] or {}
            idx = block.get("contentBlockIndex", 0)
            delta = block.get("delta") or {}

            if "text" in delta and delta["text"]:
                out.append(StreamEvent(EventKind.TEXT, text=delta["text"]))

            tool_delta = delta.get("toolUse")
            if tool_delta:
                slot = self._blocks.setdefault(idx, _new_tool_slot())
                if tool_delta.get("name"):
                    slot["name"] = tool_delta["name"]
                if tool_delta.get("toolUseId") or tool_delta.get("id"):
                    slot["id"] = tool_delta.get("toolUseId") or tool_delta.get("id")

                # Current InvokeHarness contract: `input` is a partial JSON
                # *string*. Older previews returned a complete dict here, and
                # local fixtures used partial_json. Accept all three without
                # appending the same fragment twice if a test double sends
                # more than one spelling.
                if "input" in tool_delta:
                    value = tool_delta["input"]
                    slot["observed"] = True
                    if isinstance(value, str):
                        slot["buf"] += value
                    else:
                        slot["input"] = value
                else:
                    fragment = (tool_delta.get("partial_json")
                                if "partial_json" in tool_delta
                                else tool_delta.get("partialJson"))
                    if fragment is not None:
                        slot["observed"] = True
                        slot["buf"] += str(fragment)
            return out

        if "contentBlockStop" in event:
            idx = (event["contentBlockStop"] or {}).get("contentBlockIndex", 0)
            slot = self._blocks.pop(idx, None)
            if slot and slot["name"]:
                out.append(_tool_event(slot, idx))
            return out

        usage = self._usage(event)
        if usage is not None:
            return [usage]

        return [StreamEvent(EventKind.UNKNOWN)]

    @staticmethod
    def _usage(event: dict) -> StreamEvent | None:
        """A USAGE event, or None when this is not a usage-bearing event.

        Three shapes are accepted: usage nested under `metadata` (what the
        Converse-shaped stream sends), usage flattened onto `metadata`, and
        usage at the top level. An event carrying a `metadata` key but no
        recognisable counts still returns None so it falls through to UNKNOWN
        rather than being recorded as a free call.
        """
        meta = event.get("metadata")
        block: dict | None = None
        metrics: dict = {}

        if isinstance(meta, dict):
            metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
            nested = meta.get("usage")
            block = nested if isinstance(nested, dict) else meta
        elif isinstance(event.get("usage"), dict):
            block = event["usage"]

        if not isinstance(block, dict):
            return None

        counts = {name: _pick(block, names) for name, names in _TOKEN_FIELDS.items()}
        if counts["input"] is None and counts["output"] is None:
            return None

        return StreamEvent(
            EventKind.USAGE,
            input_tokens=counts["input"] or 0,
            output_tokens=counts["output"] or 0,
            cached_tokens=counts["cached"],
            reasoning_tokens=counts["reasoning"],
            latency_ms=_pick(metrics, ("latencyMs", "latency_ms")),
        )

    def flush(self) -> list[StreamEvent]:
        """Emit any tool blocks left open when the stream ended."""
        out: list[StreamEvent] = []
        for index, slot in self._blocks.items():
            if slot["name"]:
                out.append(_tool_event(slot, index))
        self._blocks.clear()
        return out
