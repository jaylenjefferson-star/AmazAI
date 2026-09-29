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
from collections.abc import Callable
from datetime import datetime, timezone

from amazai import agentcore, keys as K
from amazai.store import Conflict, Store, new_id, now_iso

RUNTIME_KIND = "standard"
READY = "ready"
PROVISIONING = "provisioning"
FAILED = "failed"
STALE_CLAIM_SECONDS = 90
WAIT_SECONDS = 20.0
POLL_SECONDS = 0.25
HARNESS_READY_SECONDS = 18.0
CLAIM_RENEW_SECONDS = 30.0
# The HTTP API Lambda and API Gateway both die at 30s. A request must return
# before that, and must not be the thing that waits out a two-minute create.
REQUEST_WAIT_SECONDS = 2.0
REQUEST_READY_SECONDS = 8.0
# Signup warm-up runs on the API Lambda (30s). It only starts the create.
WARM_READY_SECONDS = 12.0
# The orchestrator has a 15-minute budget. This is the wait that can actually
# outlast harness creation measured at >2 minutes on this account.
WORKER_WAIT_SECONDS = 100.0
WORKER_READY_SECONDS = 240.0

# Live inventory 2026-09-28, us-west-2: this generation is CREATE_FAILED while
# generation 2 of the same digest is READY. The digest is not an owner id.
STUCK_SHARED_HARNESS = "amazai_shared_6c5bb78ac28b_3"

_READY_STATUSES = frozenset({"READY", "ACTIVE"})
_TERMINAL_STATUSES = frozenset({
    "FAILED", "CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED",
    "DELETING", "DELETED",
})


class RuntimeUnavailable(RuntimeError):
    """The account runtime could not be made ready for a new Bot or run."""


class StillCreating(RuntimeUnavailable):
    """AWS has not finished. The runtime row stays PROVISIONING.

    Stamping FAILED here is what made the next retry rotate past a harness
    that was about to become READY, and what made the console give up.
    """


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


def generation_index(name: str) -> int:
    """1 for `amazai_shared_<digest>`, N for `amazai_shared_<digest>_N`."""
    tail = (name or "").rsplit("_", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return 1


def choose_ready_harness(records: list[dict]) -> dict | None:
    """The newest READY harness, ignoring CREATE_FAILED siblings.

    Live shape this exists for: `amazai_shared_6c5bb78ac28b_3` is
    CREATE_FAILED and generation 2 of that digest is READY. Rotating to
    generation 4 rediscovers the failure and never looks back.
    """
    ready = [r for r in records
             if r.get("name") and str(r.get("status") or "").upper() in _READY_STATUSES]
    if not ready:
        return None
    return max(ready, key=lambda r: generation_index(str(r["name"])))


def _generation_names(owner_id: str, row: dict) -> list[str]:
    """Current name, then older generations, never a higher one.

    Capped so a corrupt `generation` cannot list the whole account.
    """
    current = int(row.get("generation") or 1)
    hi = min(max(current, 1), 8)
    names: list[str] = []
    explicit = row.get("harnessName")
    if explicit:
        names.append(explicit)
    for gen in range(hi, 0, -1):
        name = shared_harness_name(owner_id, gen)
        if name not in names:
            names.append(name)
    return names


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
        row = store.try_get(_pk(store), _sk(), consistent=True)
        if not row:
            return None
        ready = _ready_arn(row, role_arn)
        if ready:
            return ready
        if row.get("state") == FAILED or _claim_is_stale(row):
            return None
    return None


def _generation_of(owner_id: str, name: str) -> int:
    for gen in range(8, 0, -1):
        if shared_harness_name(owner_id, gen) == name:
            return gen
    return generation_index(name)


def _adopt_if_actually_ready(store: Store, row: dict, role_arn: str,
                             core: agentcore.AgentCore) -> str | None:
    """A live second opinion before `_take_claim` rotates past a good harness.

    The row's `state`/`rotateName` are a cache. Two production shapes both
    left a READY harness unused:

    * The wait budget is shorter than create sometimes takes, so a timeout
      used to mark the row FAILED while that same harness later reached READY.
    * A later generation is CREATE_FAILED (`amazai_shared_6c5bb78ac28b_3`)
      while an earlier one (generation 2) is READY. Checking only the name
      on the row rotates to generation 4 and never looks back.

    Walk the name on the row, then older generations. A miss falls through
    to the normal claim path. A live PROVISIONING claim is not our business.
    """
    if row.get("state") == PROVISIONING or not row.get("harnessName"):
        return None
    for name in _generation_names(store.owner_id, row):
        adopted = _adopt_named(store, row, role_arn, core, name)
        if adopted:
            return adopted
    return None


def _adopt_named(store: Store, row: dict, role_arn: str,
                 core: agentcore.AgentCore, name: str) -> str | None:
    try:
        harness_arn = core.find_harness(name)
        if not harness_arn:
            return None
        record = _harness(core.get_harness(harness_arn))
        if record.get("executionRoleArn") not in (None, role_arn):
            return None
        if str(record.get("status") or "").upper() not in _READY_STATUSES:
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        store.update(_pk(store), _sk(), {
            "state": READY, "harnessArn": harness_arn, "harnessName": name,
            "generation": _generation_of(store.owner_id, name),
            "executionRoleArn": role_arn, "readyAt": now_iso(),
            "error": None, "rotateName": False,
        }, expect={"claimToken": row.get("claimToken"), "state": row.get("state")})
        return harness_arn
    except Conflict:
        # Someone else already moved the row on since it was read -- adopt
        # whatever they left rather than fight over which write wins.
        fresh = store.try_get(_pk(store), _sk(), consistent=True)
        return _ready_arn(fresh, role_arn) if fresh else None


def _release_claim(store: Store, token: str, exc: BaseException) -> None:
    """Keep PROVISIONING and drop the lease so the long worker can take it.

    A fresh `claimedAt` would make that worker wait out STALE_CLAIM_SECONDS
    doing nothing, which is how a 30s API timeout used to strand the create.
    """
    try:
        store.update(_pk(store), _sk(), {
            "state": PROVISIONING,
            "claimedAt": "1970-01-01T00:00:00Z",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "rotateName": False,
        }, expect={"claimToken": token, "state": PROVISIONING})
    except Conflict:
        pass


def _fail_claim(store: Store, token: str, exc: BaseException, *, rotate: bool) -> None:
    try:
        store.update(_pk(store), _sk(), {
            "state": FAILED, "failedAt": now_iso(),
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "rotateName": rotate,
        }, expect={"claimToken": token, "state": PROVISIONING})
    except Conflict:
        pass


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
            row = store.get(_pk(store), _sk(), consistent=True)

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
        }, expect={
            "claimToken": old_token,
            "state": row.get("state"),
            "claimedAt": row.get("claimedAt"),
        })
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
    renew: Callable[[], None] | None = None,
) -> None:
    """Refuse a wrong-role harness and wait until AWS says it is runnable."""
    deadline = time.monotonic() + max(0.0, seconds)
    next_renew = time.monotonic()
    while True:
        if renew and time.monotonic() >= next_renew:
            renew()
            next_renew = time.monotonic() + CLAIM_RENEW_SECONDS
        record = _harness(core.get_harness(harness_arn))
        actual_role = record.get("executionRoleArn")
        if actual_role and actual_role != role_arn:
            raise HarnessRejected(
                "the recovered account harness uses a different execution role; "
                "refusing to share a wider IAM boundary")
        status = str(record.get("status") or "").upper()
        if status in {"READY", "ACTIVE"}:
            return
        if status in _TERMINAL_STATUSES:
            detail = record.get("failureReason") or status
            raise HarnessRejected(f"the account harness entered {status}: {detail}")
        if time.monotonic() >= deadline:
            # Non-terminal, including CREATING. Callers must not record FAILED.
            label = status or "CREATING"
            raise StillCreating(
                f"the account harness is still {label}; retry this request")
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def ensure_shared_harness(
    store: Store,
    *,
    client: agentcore.AgentCore | None = None,
    role_arn: str | None = None,
    wait_seconds: float = WAIT_SECONDS,
    ready_wait_seconds: float = HARNESS_READY_SECONDS,
    release_on_timeout: bool = True,
    takeover_after_wait: bool = False,
    _takeover_used: bool = False,
) -> str:
    """Find or create the owner's one standard harness and return its ARN.

    The deterministic provider name is also crash recovery. If CreateHarness
    succeeded and Lambda died before DynamoDB was updated, the next claimant
    discovers that harness through ListHarnesses instead of leaking another.
    """
    role = _role(role_arn)
    core = client or agentcore.AgentCore()
    row = store.try_get(_pk(store), _sk())
    if row:
        ready = _ready_arn(row, role)
        if ready:
            return ready
        recovered = _adopt_if_actually_ready(store, row, role, core)
        if recovered:
            return recovered

    token, name = _take_claim(store, role)
    if token == "":
        return store.get(_pk(store), _sk(), consistent=True)["harnessArn"]
    if token == "waiting":
        ready = _wait_for_other(store, role, wait_seconds)
        if ready:
            return ready
        # The request path must not steal a live claim: that is how one slow
        # create became two abandoned callers inside API Gateway's 30s. The
        # long worker is the exception. It was invoked to outlive the holder,
        # and once that holder's lease is stale it continues the same name.
        if takeover_after_wait and not _takeover_used:
            return ensure_shared_harness(
                store, client=core, role_arn=role, wait_seconds=0,
                ready_wait_seconds=ready_wait_seconds,
                release_on_timeout=release_on_timeout,
                takeover_after_wait=False, _takeover_used=True,
            )
        raise RuntimeUnavailable(
            "the account runtime is still being provisioned; retry this request")

    try:
        harness_arn = core.find_harness(name)
        if not harness_arn:
            harness_arn = core.create_harness(
                name=name, execution_role_arn=role, tool_names=[])
        # CreateHarness is asynchronous. Marking a logical Bot active before
        # READY recreates the original failure as a model error on its first
        # message. GetHarness also lets us verify that a crash-recovered name
        # did not point at a harness with a wider role.
        def renew_claim() -> None:
            store.update(_pk(store), _sk(), {"claimedAt": now_iso()}, expect={
                "claimToken": token, "state": PROVISIONING,
            })

        _wait_until_ready(
            core, harness_arn, role, seconds=ready_wait_seconds,
            renew=renew_claim)
        try:
            store.update(_pk(store), _sk(), {
                "state": READY, "harnessArn": harness_arn,
                "executionRoleArn": role, "readyAt": now_iso(), "error": None,
            }, expect={"claimToken": token, "state": PROVISIONING})
            return harness_arn
        except Conflict:
            # A takeover and READY publication can race at a lease boundary.
            # Never move the row backward: strongly adopt a winner that already
            # published, otherwise report that this caller lost ownership.
            winner = store.get(_pk(store), _sk(), consistent=True)
            ready = _ready_arn(winner, role)
            if ready:
                return ready
            raise RuntimeUnavailable(
                "another provisioner took ownership of the account runtime; "
                "retry this request")
    except StillCreating as exc:
        # CREATING is not a failure. Leave the row PROVISIONING and, on the
        # request path, drop the lease so the orchestrator can keep polling.
        if release_on_timeout:
            _release_claim(store, token, exc)
        raise
    except HarnessRejected as exc:
        _fail_claim(store, token, exc, rotate=True)
        raise
    except RuntimeUnavailable as exc:
        # "retry this request" means someone else is still working. Stamping
        # FAILED here races that worker and is how a live create got buried.
        if "retry this request" not in str(exc):
            _fail_claim(store, token, exc, rotate=False)
        raise
    except Exception as exc:
        _fail_claim(store, token, exc, rotate=False)
        raise RuntimeUnavailable(
            f"the account runtime could not be provisioned ({type(exc).__name__}: "
            f"{str(exc)[:200]})") from exc


def _dedicated_harness(agent: dict) -> str | None:
    explicit = agent.get("dedicatedHarnessArn")
    if explicit:
        return explicit
    # Rows created before account runtimes had only `harnessArn`; that value is
    # their dedicated rollback target. A shared-mode row's compatibility ARN
    # is not one -- treating it as dedicated makes the rollback switch lie.
    if agent.get("runtimeMode") != "shared":
        return agent.get("harnessArn")
    return None


def provision_bot(store: Store, agent: dict, *, client: agentcore.AgentCore | None = None,
                  wait_seconds: float | None = None,
                  ready_wait_seconds: float | None = None,
                  release_on_timeout: bool = True) -> dict:
    """Attach a new logical Bot to shared compute, or create a dedicated fallback.

    `AMAZAI_SHARED_RUNTIME=false` preserves the former one-harness-per-Bot
    behavior for a deliberate rollback. It does not move runs already pinned.
    """
    role = _role()
    core = client or agentcore.AgentCore()
    if shared_enabled():
        kwargs: dict = {"release_on_timeout": release_on_timeout}
        if wait_seconds is not None:
            kwargs["wait_seconds"] = wait_seconds
        if ready_wait_seconds is not None:
            kwargs["ready_wait_seconds"] = ready_wait_seconds
        harness_arn = ensure_shared_harness(store, client=core, role_arn=role, **kwargs)
        mode = "shared"
        changes = {
            "harnessArn": harness_arn,       # compatibility for existing readers
            "sharedHarnessArn": harness_arn,
        }
        dedicated = _dedicated_harness(agent)
        if dedicated:
            changes["dedicatedHarnessArn"] = dedicated
    else:
        harness_arn = core.create_harness(
            name=f"amazai_{agent['agentId']}", execution_role_arn=role,
            tool_names=agent.get("allowedTools") or [])
        _wait_until_ready(core, harness_arn, role)
        mode = "dedicated"
        changes = {
            "harnessArn": harness_arn,
            "dedicatedHarnessArn": harness_arn,
        }

    return store.update(K.agent_pk(store.owner_id, agent["agentId"]), "META", {
        **changes,
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

    dedicated_target = _dedicated_harness(agent)
    if legacy or dedicated or not shared_enabled():
        harness_arn = dedicated_target
        if not harness_arn:
            raise RuntimeUnavailable(
                f"Bot {agent.get('agentId')!r} has no dedicated rollback harness; "
                "run scripts/provision_agents.py --dedicated before disabling the "
                "account runtime")
    else:
        try:
            harness_arn = ensure_shared_harness(store, client=client)
            mode = "shared"
        except RuntimeUnavailable:
            harness_arn = dedicated_target
            mode = "dedicated-fallback"
            if not harness_arn:
                raise

    if not harness_arn:
        raise RuntimeUnavailable(f"Bot {agent.get('agentId')!r} has no runnable harness")

    try:
        return store.update(run["pk"], "META", {
            "runtimeHarnessArn": harness_arn,
            "runtimeMode": mode,
            "runtimePinnedAt": now_iso(),
        }, expect={"sessionId": run["sessionId"]},
           expect_absent_or_null=("runtimeHarnessArn",))
    except Conflict:
        # A duplicate Lambda delivery can run the same handler twice. The first
        # worker owns the choice; the loser adopts the complete pair from the
        # row instead of overwriting it with a fallback it resolved later.
        winner = store.get(run["pk"], "META", consistent=True)
        if winner.get("runtimeHarnessArn"):
            return winner
        raise


def for_exec(
    store: Store,
    agent: dict,
    *,
    client: agentcore.AgentCore | None = None,
) -> tuple[str, str]:
    """The account harness and Bot-scoped session for deterministic shell use."""
    session_id = K.bot_session_id(store.owner_id, agent["agentId"],
                                  f"dm-{agent['agentId']}")
    dedicated = _dedicated_harness(agent)
    if agent.get("runtimeMode") == "dedicated" or not shared_enabled():
        harness_arn = dedicated
        if not harness_arn:
            raise RuntimeUnavailable(
                f"Bot {agent.get('agentId')!r} has no dedicated rollback harness; "
                "provision one before disabling the account runtime")
    else:
        try:
            harness_arn = ensure_shared_harness(store, client=client)
        except RuntimeUnavailable:
            harness_arn = dedicated
    if not harness_arn:
        raise RuntimeUnavailable(
            f"Bot {agent.get('agentId')!r} has no runtime for shell commands")
    return harness_arn, session_id
