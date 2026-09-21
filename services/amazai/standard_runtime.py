"""One restricted AgentCore harness per AmazAI owner/workspace.

A visible Bot is a logical control-plane identity. Its profile, memory, skills,
chat history, grants and approval boundary travel with each invocation; none of
those needs a distinct AWS harness. AgentCore isolates the runtime session in a
dedicated microVM, so the execution namespace is `(owner, Bot, thread)`.

This module owns the only mutable account-runtime row and the migration rule:
new v2 runs use the shared harness; old thread-only v1 runs stay on the Bot's
existing dedicated harness so an approval resume never jumps environments.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone

from amazai import agentcore, keys as K
from amazai.store import Conflict, Store, new_id, now_iso

RUNTIME_KIND = "standard"
READY = "ready"
PROVISIONING = "provisioning"
FAILED = "failed"
STALE_CLAIM_SECONDS = 90
WAIT_SECONDS = 5.0
POLL_SECONDS = 0.25
HARNESS_READY_SECONDS = 18.0


class RuntimeUnavailable(RuntimeError):
    """The account runtime could not be made ready for a new Bot or run."""


class HarnessRejected(RuntimeUnavailable):
    """A discovered harness is terminal or violates the restricted-role boundary.

    The next provisioning claim rotates to a new deterministic generation;
    retrying the same provider name would rediscover the same unusable object
    forever.
    """


def shared_enabled() -> bool:
    """Feature switch for new runs; existing pinned runs ignore it.

    Default-on is the product decision. `false` is a deployment rollback for
    owners whose existing Bots still have dedicated harnesses.
    """
    raw = os.environ.get("AMAZAI_SHARED_RUNTIME", "true").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeUnavailable(
        f"AMAZAI_SHARED_RUNTIME must be true or false, got {raw!r}")


def shared_harness_name(owner_id: str, generation: int = 1) -> str:
    """Provider-safe and deterministic, without exposing an Auth0 subject."""
    digest = hashlib.sha256(owner_id.encode()).hexdigest()[:12]
    suffix = "" if generation <= 1 else f"_{generation}"
    return f"amazai_shared_{digest}{suffix}"


def _pk(store: Store) -> str:
    return K.user_pk(store.owner_id)


def _sk() -> str:
    return K.runtime_sk(RUNTIME_KIND)


def _role(role_arn: str | None = None) -> str:
    value = (role_arn or os.environ.get("AGENT_ROLE_ARN", "")).strip()
    if not value:
        raise RuntimeUnavailable(
            "no restricted AgentCore execution role is configured for the account runtime")
    return value


def _claim_is_stale(row: dict) -> bool:
    at = row.get("claimedAt") or row.get("updatedAt")
    if not at:
        return True
    try:
        claimed = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return (datetime.now(timezone.utc) - claimed).total_seconds() >= STALE_CLAIM_SECONDS


def _ready_arn(row: dict, role_arn: str) -> str | None:
    if row.get("state") != READY or not row.get("harnessArn"):
        return None
    if row.get("executionRoleArn") != role_arn:
        raise RuntimeUnavailable(
            "the registered account runtime uses a different execution role; "
            "refusing to widen or swap its IAM boundary in place")
    return row["harnessArn"]


def _wait_for_other(store: Store, role_arn: str, seconds: float) -> str | None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        row = store.try_get(_pk(store), _sk())
        if not row:
            return None
        ready = _ready_arn(row, role_arn)
        if ready:
            return ready
        if row.get("state") == FAILED or _claim_is_stale(row):
            return None
    return None


def _take_claim(store: Store, role_arn: str) -> tuple[str, str]:
    """Return `(claim_token, harness_name)` for the one request allowed to create.

    A loser waits for the winner. A failed or stale claim can be taken over by
    an exact conditional update; two rescuers cannot both win.
    """
    token = new_id("rtclaim_")
    name = shared_harness_name(store.owner_id)
    row = store.try_get(_pk(store), _sk())

    if row is None:
        try:
            store.put({
                "pk": _pk(store), "sk": _sk(),
                "entity": "AccountRuntime", "runtimeKind": RUNTIME_KIND,
                "state": PROVISIONING, "harnessName": name, "generation": 1,
                "harnessArn": None, "executionRoleArn": role_arn,
                "claimToken": token, "claimedAt": now_iso(), "rotateName": False,
            }, unique=True)
            return token, name
        except Conflict:
            row = store.get(_pk(store), _sk())

    ready = _ready_arn(row, role_arn)
    if ready:
        return "", row.get("harnessName") or name

    if row.get("executionRoleArn") != role_arn:
        raise RuntimeUnavailable(
            "the account runtime claim names a different execution role; "
            "refusing to race a policy change")

    if row.get("state") == PROVISIONING and not _claim_is_stale(row):
        return "waiting", row.get("harnessName") or name

    old_token = row.get("claimToken")
    if not old_token:
        raise RuntimeUnavailable("the account runtime has an invalid claim record")
    generation = int(row.get("generation") or 1)
    if row.get("rotateName"):
        generation += 1
        name = shared_harness_name(store.owner_id, generation)
    try:
        store.update(_pk(store), _sk(), {
            "state": PROVISIONING, "claimToken": token, "claimedAt": now_iso(),
            "harnessName": name, "generation": generation,
            "harnessArn": None, "rotateName": False, "error": None,
        }, expect={"claimToken": old_token})
        return token, name
    except Conflict:
        return "waiting", row.get("harnessName") or name


def _harness(row: dict) -> dict:
    return row.get("harness") if isinstance(row.get("harness"), dict) else row


def _wait_until_ready(
    core: agentcore.AgentCore,
    harness_arn: str,
    role_arn: str,
    *,
    seconds: float = HARNESS_READY_SECONDS,
) -> None:
    """Refuse a wrong-role harness and wait until AWS says it is runnable."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        record = _harness(core.get_harness(harness_arn))
        actual_role = record.get("executionRoleArn")
        if actual_role and actual_role != role_arn:
            raise HarnessRejected(
                "the recovered account harness uses a different execution role; "
                "refusing to share a wider IAM boundary")
        status = str(record.get("status") or "").upper()
        if status in {"READY", "ACTIVE"}:
            return
        if status in {"FAILED", "CREATE_FAILED", "DELETING", "DELETED"}:
            detail = record.get("failureReason") or status
            raise HarnessRejected(f"the account harness entered {status}: {detail}")
        if time.monotonic() >= deadline:
            raise RuntimeUnavailable(
                f"the account harness is still {status or 'not ready'}; retry this request")
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def ensure_shared_harness(
    store: Store,
    *,
    client: agentcore.AgentCore | None = None,
    role_arn: str | None = None,
    wait_seconds: float = WAIT_SECONDS,
    ready_wait_seconds: float = HARNESS_READY_SECONDS,
) -> str:
    """Find or create the owner's one standard harness and return its ARN.

    The deterministic provider name is also crash recovery. If CreateHarness
    succeeded and Lambda died before DynamoDB was updated, the next claimant
    discovers that harness through ListHarnesses instead of leaking another.
    """
    role = _role(role_arn)
    row = store.try_get(_pk(store), _sk())
    if row:
        ready = _ready_arn(row, role)
        if ready:
            return ready

    token, name = _take_claim(store, role)
    if token == "":
        return store.get(_pk(store), _sk())["harnessArn"]
    if token == "waiting":
        ready = _wait_for_other(store, role, wait_seconds)
        if ready:
            return ready
        # The claim may have gone stale while we waited. One recursive pass can
        # take it over; zero wait prevents an unbounded wait/recursion loop.
        token, name = _take_claim(store, role)
        if token in {"", "waiting"}:
            if token == "":
                return store.get(_pk(store), _sk())["harnessArn"]
            raise RuntimeUnavailable(
                "the account runtime is still being provisioned; retry this request")

    core = client or agentcore.AgentCore()
    try:
        harness_arn = core.find_harness(name)
        if not harness_arn:
            harness_arn = core.create_harness(
                name=name, execution_role_arn=role, tool_names=[])
        # CreateHarness is asynchronous. Marking a logical Bot active before
        # READY recreates the original failure as a model error on its first
        # message. GetHarness also lets us verify that a crash-recovered name
        # did not point at a harness with a wider role.
        _wait_until_ready(core, harness_arn, role, seconds=ready_wait_seconds)
        store.update(_pk(store), _sk(), {
            "state": READY, "harnessArn": harness_arn,
            "executionRoleArn": role, "readyAt": now_iso(), "error": None,
        }, expect={"claimToken": token})
        return harness_arn
    except Exception as exc:
        try:
            store.update(_pk(store), _sk(), {
                "state": FAILED, "failedAt": now_iso(),
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "rotateName": isinstance(exc, HarnessRejected),
            }, expect={"claimToken": token})
        except Conflict:
            pass
        raise RuntimeUnavailable(
            f"the account runtime could not be provisioned ({type(exc).__name__}: "
            f"{str(exc)[:200]})") from exc


def provision_bot(store: Store, agent: dict, *, client: agentcore.AgentCore | None = None) -> dict:
    """Attach a new logical Bot to shared compute, or create a dedicated fallback.

    `AMAZAI_SHARED_RUNTIME=false` preserves the former one-harness-per-Bot
    behavior for a deliberate rollback. It does not move runs already pinned.
    """
    role = _role()
    if shared_enabled():
        harness_arn = ensure_shared_harness(store, client=client, role_arn=role)
        mode = "shared"
    else:
        core = client or agentcore.AgentCore()
        harness_arn = core.create_harness(
            name=f"amazai_{agent['agentId']}", execution_role_arn=role,
            tool_names=agent.get("allowedTools") or [])
        mode = "dedicated"

    return store.update(K.agent_pk(agent["agentId"]), "META", {
        "harnessArn": harness_arn,
        "executionRoleArn": role,
        "runtimeMode": mode,
        "status": "active", "state": "active",
    })


def pin_run(
    store: Store,
    run: dict,
    agent: dict,
    *,
    client: agentcore.AgentCore | None = None,
) -> dict:
    """Persist the exact harness a run must use for every retry/resume.

    Old v1 sessions stay dedicated. New v2 sessions use shared compute unless
    the Bot explicitly requires dedicated execution or the deployment switch
    is off. If shared provisioning fails, an existing dedicated harness is a
    safe availability fallback for this run and is pinned as such.
    """
    if run.get("runtimeHarnessArn"):
        return run

    legacy = not K.is_bot_session_id(run.get("sessionId", ""))
    dedicated = agent.get("runtimeMode") == "dedicated"
    mode = "dedicated"

    if legacy or dedicated or not shared_enabled():
        harness_arn = agent.get("harnessArn")
    else:
        try:
            harness_arn = ensure_shared_harness(store, client=client)
            mode = "shared"
        except RuntimeUnavailable:
            harness_arn = agent.get("harnessArn")
            mode = "dedicated-fallback"
            if not harness_arn:
                raise

    if not harness_arn:
        raise RuntimeUnavailable(f"Bot {agent.get('agentId')!r} has no runnable harness")

    return store.update(run["pk"], "META", {
        "runtimeHarnessArn": harness_arn,
        "runtimeMode": mode,
        "runtimePinnedAt": now_iso(),
    }, expect={"sessionId": run["sessionId"]})


def for_exec(
    store: Store,
    agent: dict,
    *,
    client: agentcore.AgentCore | None = None,
) -> tuple[str, str]:
    """The account harness and Bot-scoped session for deterministic shell use."""
    session_id = K.bot_session_id(store.owner_id, agent["agentId"],
                                  f"dm-{agent['agentId']}")
    if agent.get("runtimeMode") == "dedicated" or not shared_enabled():
        harness_arn = agent.get("harnessArn")
    else:
        try:
            harness_arn = ensure_shared_harness(store, client=client)
        except RuntimeUnavailable:
            harness_arn = agent.get("harnessArn")
    if not harness_arn:
        raise RuntimeUnavailable(
            f"Bot {agent.get('agentId')!r} has no runtime for shell commands")
    return harness_arn, session_id
