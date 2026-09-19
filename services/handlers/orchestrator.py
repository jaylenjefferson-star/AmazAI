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

from amazai import (agentcore, agents as A, approvals, collab, connectors, cost,
                    keys as K, memory, policy, redact, router, runs, skills)
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
    memories = memory.visible(store.query(K.agent_pk(run["agentId"]), sk_prefix="MEM#", limit=50))
    # Shared user memory (name, timezone, standing preferences) is visible to
    # every agent's context alongside its own, on by default -- see
    # docs/architecture/16-grokbot-ux-alignment.md §4 and open question 1.
    # `memory.visible` drops anything revoked/expired/still-proposed so a
    # publish approval or a revoke takes effect on the very next turn.
    memories += memory.visible(store.query(K.user_pk(store.owner_id), sk_prefix="MEM#", limit=50))
    # Task-scoped memory only exists for a run that is actually part of that
    # task: its own runId, or the taskId a priority message spawned it under
    # (`trigger.taskId`, set only by an already-authorized send -- see
    # collab.send). A run outside that task never queries this partition, so
    # task memory cannot cross a task boundary by construction.
    effective_task_id = (run.get("trigger") or {}).get("taskId") or run["runId"]
    memories += memory.visible(store.query(K.task_pk(effective_task_id), sk_prefix="MEM#", limit=50))
    assigned_skills = skills.assigned_active_skills(store, run["agentId"])

    messages = agentcore.build_messages(history, room=thread.get("kind") == "room")
    if event.get("resume") and event.get("resumeNote"):
        # D4 fallback path: if invoke_harness cannot take a native toolResult
        # continuation, the decision is delivered as a user turn instead. Same
        # state machine either way.
        messages.append({"role": "user", "content": [{"text": event["resumeNote"]}]})

    system_prompt = agentcore.build_system_prompt(agent, memories, skills=assigned_skills)


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

    if name == "propose_agent":
        # This is deliberately a proposal rather than an agent-originated
        # create. The model can nominate a role, but cannot choose grants,
        # optional tools, or an open-ended budget; those fields are fixed
        # below and the human approval is bound to the exact proposal.
        proposal = _agent_creation_proposal(args, parent_agent_id=agent["agentId"])
        A.validate_profile(proposal)
        # The always-approve floor includes agent.create. Calling the central
        # policy gate here keeps that invariant explicit if the policy evolves.
        policy.evaluate("agent.create", Capability.ADMIN)
        approval = approvals.request(
            store, run,
            action="agent.create", arguments=proposal,
            why=args.get("why", "A separate companion is needed for this lane."),
            capability=Capability.ADMIN,
            tool_use_id=parsed.tool_use_id,
            target={"parentAgentId": agent["agentId"]},
            reversible=False,
        )
        ev.action(seq, "agent.create", "agent creation proposed", approvalId=approval["approvalId"])
        return {"pause": True, "approval": approval}

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

    if name == "message_agent":
        try:
            result = _message_agent(store, run, agent, args)
        except collab.MessagingError as exc:
            ev.error(seq, "terminal", str(exc))
            push.tool(run["runId"], run["threadId"], "message_agent", f"blocked: {exc}")
            return {"pause": False}
        ev.action(seq, "message_agent", f"to {args.get('to')}"
                 f" ({'priority' if result['priorityGranted'] else 'deferred'})",
                 messageId=result["message"]["messageId"])
        push.tool(run["runId"], run["threadId"], "message_agent",
                 f"-> {args.get('to')}: {args.get('text','')[:120]}")
        return {"pause": False}

    if name == "remember":
        # Direct write, no approval: scope is limited to this agent's own
        # namespace or the current task's own partition -- never every agent.
        scope = args.get("scope", "agent")
        try:
            if scope == "agent":
                pk = K.agent_pk(agent["agentId"])
            elif scope == "task":
                task_id = args.get("task_id") or (run.get("trigger") or {}).get("taskId") or run["runId"]
                pk = K.task_pk(task_id)
            else:
                raise memory.ValidationError(
                    f"remember only writes scope=agent or scope=task, got {scope!r}; "
                    "use propose_shared_memory to reach every agent")
            row = memory.plan_write(args, pk, scope=scope, source="agent", author=agent["agentId"])
        except memory.ValidationError as exc:
            ev.error(seq, "terminal", str(exc))
            return {"pause": False}
        store.put(row)
        ev.action(seq, "remember", f"{scope} memory saved", memId=row["memId"])
        return {"pause": False}

    if name == "propose_shared_memory":
        # Same pattern as propose_agent/propose_skill: the model nominates, a
        # person decides. Nothing here reaches shared_user until _decide
        # approves it -- see memory.plan_write's status="proposed" default.
        try:
            fields = memory.validate(args, scope="shared_user")
        except memory.ValidationError as exc:
            ev.error(seq, "terminal", str(exc))
            return {"pause": False}
        proposal = {**fields, "proposedBy": agent["agentId"]}
        policy.evaluate("memory.publish", Capability.WRITE)
        approval = approvals.request(
            store, run,
            action="memory.publish", arguments=proposal,
            why=args.get("why", "The operator should know this."),
            capability=Capability.WRITE,
            tool_use_id=parsed.tool_use_id,
            reversible=True,
        )
        ev.action(seq, "memory.publish", "shared memory proposed", approvalId=approval["approvalId"])
        return {"pause": True, "approval": approval}

    if name == "propose_skill":
        # Same pattern as propose_agent: the model nominates, a person
        # decides. A proposed skill is excluded from every agent's context
        # until approved and assigned -- see skills.assigned_active_skills.
        try:
            proposal = skills.validate_skill(args)
        except skills.ValidationError as exc:
            ev.error(seq, "terminal", str(exc))
            return {"pause": False}
        proposal["proposedBy"] = agent["agentId"]
        policy.evaluate("skill.create", Capability.ADMIN)
        approval = approvals.request(
            store, run,
            action="skill.create", arguments=proposal,
            why=args.get("why", "A reusable skill would help future runs."),
            capability=Capability.ADMIN,
            tool_use_id=parsed.tool_use_id,
            reversible=True,
        )
        ev.action(seq, "skill.create", "skill proposed", approvalId=approval["approvalId"])
        return {"pause": True, "approval": approval}

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


def _agent_creation_proposal(args: dict, *, parent_agent_id: str) -> dict:
    """Normalize the only fields a model may nominate for a child agent.

    These limits are intentionally below the normal human Create-a-Bot
    defaults. A newly approved companion has a useful, bounded first session;
    granting connectors, optional computer tools, or a larger budget remains a
    distinct owner action in the console.
    """
    return {
        "name": args.get("name", ""),
        "role": args.get("role", ""),
        "description": args.get("description", ""),
        "systemPrompt": args.get("systemPrompt", ""),
        "modelTier": args.get("modelTier"),
        "workingStyle": args.get("workingStyle", "collaborative"),
        "avatar": args.get("avatar") or {},
        "parentAgentId": parent_agent_id,
        "tools": [],
        "grants": [],
        "budget": {
            "perRunUsd": 0.50,
            "perMonthUsd": 5.00,
            "maxConcurrentRuns": 1,
            "maxToolCallsPerRun": 20,
            "onCeiling": "hard_stop",
        },
    }


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


def _message_agent(store: Store, run: dict, agent: dict, args: dict) -> dict:
    """Task/context-bound agent-to-agent messaging.

    `collab.send` is the authorization boundary: it requires task_id or
    collaboration_context_id, checks both agents are participants (or an
    org escalation policy applies), and enforces hop depth / per-task
    message ceilings by raising `collab.MessagingError`. `priority` only
    ever *requests* an expedited wake; `collab.may_wake_now` still has to
    clear concurrency and budget before a run is actually spawned for the
    recipient -- exactly the same gates any other trigger goes through in
    `_drive`.
    """
    to_agent_id = (args.get("to") or "").strip()
    if not to_agent_id:
        raise collab.MessagingError("message_agent requires 'to'")
    if to_agent_id == agent["agentId"]:
        raise collab.MessagingError("an agent cannot message itself")

    to_agent = store.try_get(K.agent_pk(to_agent_id), "META")
    if to_agent is None or to_agent.get("status") not in A.RUNNABLE:
        raise collab.MessagingError(f"no such active recipient {to_agent_id!r}")

    outcome = collab.send(store, sender_agent_id=agent["agentId"],
                          recipient_agent_id=to_agent_id, args=args)
    context = outcome["context"]
    message = outcome["message"]

    woke = False
    if outcome["priority_granted"]:
        limits = collab.limits_for_org(store)
        allowed, _reason = collab.may_wake_now(store, to_agent, limits)
        if allowed:
            new_run = runs.create(store, agent_id=to_agent_id, thread_id=context.thread_id,
                                  goal=message["text"],
                                  trigger={"type": "agent", "fromAgentId": agent["agentId"],
                                          "taskId": message.get("taskId"),
                                          "collaborationContextId": message.get("collaborationContextId"),
                                          "traceId": message["traceId"]})
            _invoke_orchestrator_async(new_run["runId"], store.owner_id)
            woke = True

    return {"message": message, "priorityGranted": outcome["priority_granted"], "woke": woke}


def _invoke_orchestrator_async(run_id: str, owner_id: str) -> None:
    """Wake the recipient's own run. Same fire-and-forget invoke api.py uses
    for a resume; duplicated rather than imported to avoid a handler-to-
    handler import."""
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if not fn:
        return
    boto3.client("lambda").invoke(
        FunctionName=fn, InvocationType="Event",
        Payload=json.dumps({"runId": run_id, "ownerId": owner_id}).encode(),
    )


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
    return cost.spent_this_month(store, agent_id)


def _budget_for(agent: dict) -> Budget:
    return cost.budget_for_agent(agent)


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
