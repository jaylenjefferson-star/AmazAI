"""AgentCore compute provider: a thin adapter over the existing runtime.

Implements the compute interface from `base.py` on top of the existing
`amazai.agentcore.AgentCore` client. This is a passthrough: it does NOT
reimplement or modify AgentCore, so existing runtime behavior
(`docs/architecture/05-run-lifecycle.md`) is unchanged. It exists only so the
same lifecycle shape (`acquire -> wait_until_ready -> execute -> release`)
covers both AgentCore and EC2 Desktop.

The wrapped ``AgentCore`` is injected via ``__init__`` (mirroring
``AgentCore.__init__``'s ``runtime=`` / ``control=`` injection), so the adapter
is testable without AWS.
"""

from __future__ import annotations

from typing import Any

from amazai.agentcore import AgentCore

from .types import ComputeProvider


class AgentCoreProvider:
    """Adapts the existing ephemeral AgentCore runtime to the compute interface."""

    def __init__(self, core: AgentCore | None = None) -> None:
        """Accept an injected ``AgentCore`` for testability.

        Falls back to constructing a default ``AgentCore`` (which itself lazily
        builds boto3 clients) when none is supplied.
        """
        self._core = core if core is not None else AgentCore()

    @property
    def provider(self) -> ComputeProvider:
        return ComputeProvider.AGENTCORE

    @property
    def core(self) -> AgentCore:
        """The wrapped ``AgentCore`` client."""
        return self._core

    def acquire(self, run: dict, agent: dict, *, store: Any = None) -> dict:
        """No-op acquire: AgentCore sessions are ephemeral.

        The microVM/session is acquired implicitly by ``invoke_stream``/``exec``
        and managed by the runtime, so there is nothing to provision here. Return
        an immediately-ready handle carrying the run/agent context.
        """
        return {"run": run, "agent": agent, "ready": True}

    def wait_until_ready(self, handle: Any, *, store: Any = None) -> bool:
        """Always ready: AgentCore sessions need no readiness wait."""
        return True

    def execute(self, handle: Any, *, store: Any = None, mode: str = "invoke_stream",
                **kwargs: Any) -> Any:
        """Delegate execution to the wrapped ``AgentCore`` without reimplementing it.

        ``mode='invoke_stream'`` (default) delegates to ``AgentCore.invoke_stream``
        for a streaming agent turn; ``mode='exec'`` delegates to ``AgentCore.exec``
        for a raw shell command. All keyword arguments pass straight through.
        """
        if mode == "exec":
            return self._core.exec(**kwargs)
        if mode == "invoke_stream":
            return self._core.invoke_stream(**kwargs)
        raise ValueError(f"unknown execute mode: {mode!r}")

    def release(self, handle: Any, *, store: Any = None) -> None:
        """No-op release: AgentCore teardown is handled by the runtime.

        Sessions expire via AgentCore's 14-day TTL and the existing
        sync-at-run-end path, so there is nothing to tear down explicitly here.
        """
        return None
