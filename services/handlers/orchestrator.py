"""The agent loop.

Streams a turn from the harness, pushes deltas to the console, and stops when
the agent asks for an approval.

The important structural fact: shell, file_operations, browser and gateway
targets all execute INSIDE the harness microVM. They never round-trip through
this Lambda. Only the inline functions -- request_approval and handoff -- come
back for us to answer, which is what keeps this loop small.

Nothing is held open during a pause. The Lambda exits; the run's whole
resumable state is the RUN# row plus the session ID.
"""

from __future__ import annotations

import json
import os
import traceback

import boto3

from amazai import (agentcore, approvals, connectors, keys as K, policy,
                    redact, router, runs)
from amazai.cost import Budget, RunCost, Verdict, check as budget_check
from amazai.errors import ErrorClass, classify
from amazai.evidence import EvidenceWriter
from amazai.policy import Capability
from amazai.push import Push
from amazai.states import RunState
from amazai.store import Store, new_id, now_iso, ordered_suffix
from amazai.stream import EventKind, StreamParser

MAX_HISTORY = 40


def _owner() -> str:
    return os.environ.get("OWNER_ID", "owner")


def handler(event, context):  # noqa: ARG001
    """Invoked asynchronously with {"runId": ...} or {"runId":..., "resume": true}."""
    run_id = event["runId"]
    store = Store(event.get("ownerId") or _owner())
    run = store.get(K.run_pk(run_id), "META")

    try:
        return _drive(store, run, event)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _fail(store, run, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "runId": run_id, "error": str(exc)}


def _fail(store: Store, run: dict, message: str) -> None:
    """Terminal failure still seals evidence. A failed run must be inspectable."""
    try:
        fresh = store.get(run["pk"], "META")
        if RunState(fresh["state"]) in {RunState.COMPLETED, RunState.FAILED,
                                        RunState.CANCELLED, RunState.EXPIRED,
                                        RunState.PARTIAL}:
            return
        ev = EvidenceWriter(fresh["runId"])
        ev.error(fresh.get("cursor", {}).get("lastEventSeq", 0), "terminal", message)
        manifest = ev.seal(
            run=fresh, outcome=RunState.FAILED.value, summary=message,
            cost={"totalUsd": fresh.get("costUsd", 0.0)},
            approvals=approvals.for_run(store, fresh["pk"]),
        )
        runs.advance(store, fresh, RunState.FAILED, evidenceKey=ev.key,
                     summary=message, sealSha256=manifest.get("sealSha256"))
        Push(store).run_end(fresh["runId"], fresh["threadId"],
                            RunState.FAILED.value, message, fresh.get("costUsd", 0.0))
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _drive(store: Store, run: dict, event: dict) -> dict:
    push = Push(store)
    agent = store.get(K.agent_pk(run["agentId"]), "META")

    if agent.get("state") != "active":
        _fail(store, run, f"agent {agent.get('name')} is {agent.get('state')}")
        return {"ok": False, "reason": "agent not active"}

    model_id = (agent.get("model") or {}).get("modelId")
    if not model_id:
        # seats.json ships modelId: null until D2 is decided. Failing here with
        # a clear message beats a Bedrock error that looks like a permissions bug.
        _fail(store, run,
              "no modelId configured for this seat; resolve the Bedrock inference "
              "profile and set it in scripts/seats.json (decision D2)")
        return {"ok": False, "reason": "no model configured"}

    # --- budget, before anything is spent ---------------------------------
    budget = _budget_for(agent)
    spent_month = _spent_this_month(store, agent["agentId"])
    verdict = budget_check(budget, spent_this_run=run.get("costUsd", 0.0),
                           spent_this_month=spent_month,
                           tool_calls=run.get("toolCallCount", 0),
                           tool_errors=run.get("toolErrorCount", 0),
                           consecutive_tool_errors=run.get("consecutiveToolErrors", 0))
    if verdict.should_stop:
        _finish(store, run, RunState.FAILED, f"stopped: {verdict.reason}", push)
        return {"ok": False, "reason": verdict.reason}
    if verdict.verdict is Verdict.WARN:
        push.notification("warn", f"{agent['name']}: {verdict.reason}")

    # --- tool resolution: grants ∩ budget ∩ rate limits --------------------
    # Intersected with the org install and the catalog on every run, not
    # trusted as written. A grant row that outlived its install — a revoke
    # that raced this read, a restored backup — contributes nothing, so
    # revoking a connector removes its tools from the next schema built.
    grants = connectors.router_grants(store, run["agentId"])
    resolution = router.resolve_tools(router.ResolutionInput(
        agent_allowed_tools=frozenset(agent.get("allowedTools", [])),
        grants=grants,
        connector_covers_outcome=bool(run.get("connectorCoversOutcome")),
    ))

    # --- conversation ------------------------------------------------------
    thread = store.get(K.thread_pk(run["threadId"]), "META")
    history = store.query(K.thread_pk(run["threadId"]), sk_prefix="MSG#",
                          limit=MAX_HISTORY, ascending=True)[-MAX_HISTORY:]
    memories = store.query(K.agent_pk(run["agentId"]), sk_prefix="MEM#", limit=50)

    messages = agentcore.build_messages(history, room=thread.get("kind") == "room")
    if event.get("resume") and event.get("resumeNote"):
        # D4 fallback path: if invoke_harness cannot take a native toolResult
        # continuation, the decision is delivered as a user turn instead. Same
        # state machine either way.
        messages.append({"role": "user", "content": [{"text": event["resumeNote"]}]})

    system_prompt = agentcore.build_system_prompt(agent, memories)

    run = runs.advance(store, run, RunState.PLANNING) if run["state"] == RunState.QUEUED.value else run
    run = runs.advance(store, run, RunState.EXECUTING) if run["state"] == RunState.PLANNING.value else run
    push.state(run["runId"], run["threadId"], run["state"], run.get("costUsd", 0.0))

    # --- stream ------------------------------------------------------------
    core = agentcore.AgentCore()
    parser = StreamParser()
    ev = EvidenceWriter(run["runId"])
    cost = RunCost()
    seq = run.get("cursor", {}).get("lastEventSeq", 0)
    buffer: list[str] = []
    pending_approval: dict | None = None
    stream_error: str | None = None

    try:
        stream = core.invoke_stream(
            harness_arn=agent["harnessArn"],
            session_id=run["sessionId"],
            messages=messages,
            model_id=model_id,
            system_prompt=system_prompt,
            allowed_tools=list(resolution.tools) or None,
        )

        for raw in stream:
            for parsed in parser.feed(raw):
                seq += 1

                if parsed.kind is EventKind.TEXT:
                    buffer.append(parsed.text)
                    push.delta(run["runId"], run["threadId"], parsed.text)
                    continue

                if parsed.kind is EventKind.ERROR:
                    stream_error = parsed.error
                    break

                if parsed.kind is EventKind.TOOL_USE:
                    result = _handle_tool(store, run, agent, ev, push, resolution,
                                          parsed, seq, cost)
                    if result.get("pause"):
                        pending_approval = result["approval"]
                        break

            if stream_error or pending_approval:
                break

            if runs.is_cancelled(store, run):
                _finish(store, run, RunState.CANCELLED,
                        "cancelled by you; the in-flight action was allowed to finish",
                        push, ev=ev, cost=cost, text="".join(buffer))
                return {"ok": True, "state": RunState.CANCELLED.value}

        for parsed in parser.flush():
            if parsed.kind is EventKind.TOOL_USE:
                seq += 1
                _handle_tool(store, run, agent, ev, push, resolution, parsed, seq, cost)

    except Exception as exc:  # noqa: BLE001
        stream_error = f"{type(exc).__name__}: {exc}"

    text = "".join(buffer).strip()
    if text:
        _persist_message(store, run, agent, text, cost)

    run = store.get(run["pk"], "META")
    run = store.update(run["pk"], "META", {
        "cursor": {"turn": run.get("cursor", {}).get("turn", 0) + 1, "lastEventSeq": seq},
        "costUsd": run.get("costUsd", 0.0) + cost.total_usd,
    })
    _write_cost(store, run, agent, cost)

    # --- settle ------------------------------------------------------------
    if pending_approval:
        runs.pause_for_approval(store, run, pending_approval)
        push.approval_requested(run["runId"], run["threadId"],
                                approvals.to_card(pending_approval))
        return {"ok": True, "state": RunState.AWAITING_APPROVAL.value}

    if stream_error:
        cls = classify(stream_error, attempt=run.get("attempt", 0))
        ev.error(seq, cls.cls.value, stream_error, cls.reason)

        if cls.cls is ErrorClass.NEEDS_HUMAN:
            runs.advance(store, run, RunState.AWAITING_CONNECTOR)
            push.notification("warn", f"{agent['name']} needs you: {stream_error}")
            return {"ok": True, "state": RunState.AWAITING_CONNECTOR.value}

        if cls.retryable:
            runs.advance(store, run, RunState.RETRYING,
                         attempt=run.get("attempt", 0) + 1)
            _reinvoke(run["runId"], store.owner_id, delay_note=cls.reason)
            return {"ok": True, "state": RunState.RETRYING.value, "retry": cls.reason}

        _finish(store, run, RunState.FAILED, stream_error, push, ev=ev, cost=cost, text=text)
        return {"ok": False, "state": RunState.FAILED.value}

    _finish(store, run, RunState.COMPLETED, text or "done", push,
            ev=ev, cost=cost, text=text)
    return {"ok": True, "state": RunState.COMPLETED.value}


def _handle_tool(store, run, agent, ev, push, resolution, parsed, seq, cost) -> dict:
    """Answer an inline function call, or record an in-harness tool call."""
    name = parsed.tool_name
    args = parsed.tool_input

    if name == "request_approval":
        action = args.get("action", "unknown")
        arguments = args.get("arguments", {}) or {}
        capability = _capability_for(action, agent)

        try:
            decision = policy.evaluate(
                action, capability,
                preapproved=frozenset(agent.get("preapproved", [])),
            )
        except policy.Refused as exc:
            ev.error(seq, "terminal", str(exc))
            push.tool(run["runId"], run["threadId"], action, f"refused: {exc}")
            return {"pause": False}

        if not decision.required:
            # Already covered; tell the agent to proceed rather than pausing.
            push.tool(run["runId"], run["threadId"], action,
                      f"pre-approved ({decision.reason})")
            return {"pause": False}

        approval = approvals.request(
            store, run,
            action=action, arguments=arguments,
            why=args.get("why", ""), capability=capability,
            tool_use_id=parsed.tool_use_id,
            target=args.get("target") or {},
            reversible=args.get("reversible"),
        )
        ev.action(seq, action, "approval requested", approvalId=approval["approvalId"])
        return {"pause": True, "approval": approval}

    if name == "handoff":
        handoff = _record_handoff(store, run, args)
        ev.action(seq, "handoff", f"to {args.get('to')}", handoffId=handoff["handoffId"])
        push.handoff(run["runId"], run["threadId"], handoff)
        return {"pause": False}

    # In-harness tool (shell, browser, gateway target). We observe, not execute.
    if not router.may_call(name, resolution):
        # Should be unreachable: unresolved tools are absent from the schema.
        ev.error(seq, "terminal", f"{name} called without a grant")
        return {"pause": False}

    safe_args, redacted = redact.redact(args)
    summary = _summarise(name, safe_args)
    runs.record_event(store, run, seq, "tool", tool=name, args=safe_args,
                      redactions=redacted)
    ev.action(seq, name, summary, redactions=redacted)
    cost.add_connector(name.split(".")[0], calls=1)
    push.tool(run["runId"], run["threadId"], name, summary)

    # A connector tool is executed here, through the Pipedream proxy, rather
    # than inside the harness. The proxy injects the third party's credential
    # on its side, so the token never enters this process and cannot reach
    # model context. Everything that decided this call was allowed already
    # ran: the org install, the agent grant, and policy.evaluate.
    if _is_connector_tool(name, resolution):
        try:
            # Re-read the grants here rather than reusing the ones resolution
            # was built from. A revoke that lands mid-run is then honoured on
            # the very next tool call, not only on the next run.
            live_grants = connectors.router_grants(store, run["agentId"])
            result = connectors.invoke(
                store, _pipedream_client(), agent_id=run["agentId"], tool=name,
                arguments=args, grants=live_grants, run_id=run["runId"])
        except Exception as exc:  # noqa: BLE001
            ev.error(seq, "retryable", f"{name} failed: {type(exc).__name__}")
            return {"pause": False, "toolResult": {
                "error": f"{name} failed: {exc}"}}
        return {"pause": False, "toolResult": redact.redact(result)[0]}
    store.update(run["pk"], "META",
                 {"toolCallCount": run.get("toolCallCount", 0) + 1})
    return {"pause": False}


_pd = None


def _pipedream_client():
    """Built on first use, so a run that calls no connector never reads the
    Pipedream secret."""
    global _pd
    if _pd is None:
        from amazai.pipedream import Pipedream
        _pd = Pipedream()
    return _pd


def _is_connector_tool(name: str, resolution) -> bool:
    return name in resolution.connector_tools


def _summarise(name: str, args: dict) -> str:
    for field in ("command", "url", "path", "query", "repo"):
        if field in args:
            return f"{args[field]}"[:160]
    return json.dumps(args, default=str)[:160]


def _capability_for(action: str, agent: dict) -> Capability:
    """Look the action up in the agent's connector catalogs.

    Defaults to WRITE rather than READ: an unclassified action gets the
    stricter treatment, so a missing catalog entry cannot silently skip an
    approval.
    """
    catalog = agent.get("toolCapabilities", {}) or {}
    raw = catalog.get(action)
    if raw:
        try:
            return Capability(raw)
        except ValueError:
            pass
    return Capability.WRITE


def _record_handoff(store: Store, run: dict, args: dict) -> dict:
    handoff_id = new_id("hoff_")
    return store.put({
        "pk": run["pk"], "sk": K.handoff_sk(handoff_id),
        "entity": "Handoff", "handoffId": handoff_id,
        "gsi1pk": "HANDOFFS", "gsi1sk": f"proposed#{now_iso()}",
        "fromAgentId": run["agentId"], "toAgentId": args.get("to"),
        "goal": args.get("goal", ""), "state": args.get("state", ""),
        "constraints": args.get("constraints", []),
        "requestedAction": args.get("requestedAction", ""),
        # Always empty. Grants never travel with a handoff; the field exists to
        # say so at the schema level.
        "grantsOffered": [],
        "status": "proposed",
        "ownerAgentId": run.get("ownerAgentId") or run["agentId"],
    })


def _persist_message(store: Store, run: dict, agent: dict, text: str, cost: RunCost) -> None:
    store.put({
        "pk": K.thread_pk(run["threadId"]),
        "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "assistant",
        "author": agent.get("name"), "agentId": agent["agentId"],
        "runId": run["runId"], "text": text,
        "usage": cost.to_item(),
    })


def _write_cost(store: Store, run: dict, agent: dict, cost: RunCost) -> None:
    month = now_iso()[:7]
    store.put({
        "pk": K.cost_pk(agent["agentId"], month),
        "sk": K.run_pk(run["runId"]),
        "entity": "Cost", "runId": run["runId"], "agentId": agent["agentId"],
        **cost.to_item(),
    })


def _spent_this_month(store: Store, agent_id: str) -> float:
    month = now_iso()[:7]
    rows = store.query(K.cost_pk(agent_id, month), limit=500)
    return sum(float(r.get("totalUsd", 0.0)) for r in rows)


def _budget_for(agent: dict) -> Budget:
    b = agent.get("budget", {}) or {}
    return Budget(
        per_run_usd=float(b.get("perRunUsd", 2.0)),
        per_month_usd=float(b.get("perMonthUsd", 40.0)),
        on_ceiling=b.get("onCeiling", "hard_stop"),
        max_tool_calls_per_run=int(b.get("maxToolCallsPerRun", 60)),
    )


def _finish(store: Store, run: dict, state: RunState, summary: str, push: Push,
            *, ev: EvidenceWriter | None = None, cost: RunCost | None = None,
            text: str = "") -> None:
    fresh = store.get(run["pk"], "META")
    ev = ev or EvidenceWriter(fresh["runId"])
    cost = cost or RunCost()
    if text:
        ev.output("text", value=text[:2000])

    manifest = ev.seal(
        run=fresh, outcome=state.value, summary=summary[:2000],
        cost=cost.to_item(),
        approvals=approvals.for_run(store, fresh["pk"]),
    )
    runs.advance(store, fresh, state, evidenceKey=ev.key, summary=summary[:2000],
                 sealSha256=manifest.get("sealSha256"))
    push.run_end(fresh["runId"], fresh["threadId"], state.value, summary[:2000],
                 fresh.get("costUsd", 0.0))


def _reinvoke(run_id: str, owner_id: str, *, delay_note: str = "") -> None:
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if not fn:
        return
    boto3.client("lambda").invoke(
        FunctionName=fn, InvocationType="Event",
        Payload=json.dumps({"runId": run_id, "ownerId": owner_id,
                            "resume": True, "note": delay_note}).encode(),
    )
