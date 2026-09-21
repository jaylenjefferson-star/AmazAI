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
import time
import traceback

import boto3

from dataclasses import dataclass, field

from amazai import (agentcore, agents as A, approvals, collab, composio, connectors,
                    continuation, cost, keys as K, memory, onboarding, org, policy, provisioning, redact, review,
                    router, routines, runs, skills, threads)
from amazai.cost import Budget, RunCost, Verdict, check as budget_check
from amazai.errors import ErrorClass, classify
from amazai.evidence import EvidenceWriter
from amazai.policy import Capability
from amazai.push import Push
from amazai.states import RunState
from amazai.store import Store, new_id, now_iso, ordered_suffix
from amazai.stream import EventKind, StreamParser

MAX_HISTORY = 40

#: Model calls in one turn, each answering the tools the last one asked for. Not a
#: limit on how much a Bot may do -- it can carry on in the next message -- but a stop
#: for a loop that would otherwise run until the run's deadline.
MAX_TOOL_ROUNDS = 40

#: Wall-clock seconds a turn may spend before it stops asking the model for more. The
#: worker Lambda is killed at 15 minutes, and a run killed mid-round is left in a state
#: nobody chose; stopping cleanly with the work so far is better than either.
ROUND_BUDGET_SECONDS = 11 * 60

#: The inline tools the code answers itself. Their result goes back to the model;
#: the tools that run inside the harness (a shell, the files, a browser) never do.
#: `propose_agent` is the name a harness made before `create_agent` still carries.
ROUND_TRIP_TOOLS = frozenset(agentcore.INLINE_TOOLS) | {"propose_agent"}


@dataclass
class Turn:
    """What one invocation of the loop did, gathered as it happens.

    `steps` become the "Worked for 14s · 5 steps" trail and are stored on the
    assistant's message, so they survive a reload; `cards` are the proposals the
    run made (a connector to connect, a routine to set up). Both are written by
    code from the tool calls the run actually made, never from anything the model
    says about itself.
    """
    steps: list = field(default_factory=list)
    cards: list = field(default_factory=list)


def _step(push, run, turn, tool: str, summary: str, verdict) -> None:
    """Record one step and tell the console, with Auto Review's verdict on it."""
    entry = {"name": tool, "summary": summary, "at": now_iso(),
             "review": verdict.to_dict() if verdict else None}
    turn.steps.append(entry)
    push.tool(run["runId"], run["threadId"], tool, summary, review=entry["review"])


def _owner() -> str:
    return os.environ.get("OWNER_ID", "owner")


def handler(event, context):  # noqa: ARG001
    """Invoked asynchronously with {"runId": ...} or {"runId":..., "resume": true}."""
    run_id = event["runId"]
    store = Store(event.get("ownerId") or _owner())
    run = store.get(K.run_pk(run_id), "META")

    try:
        if event.get("cancel"):
            return _settle_paused_cancel(store, run)
        return _drive(store, run, event)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _fail(store, run, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "runId": run_id, "error": str(exc)}


def _settle_paused_cancel(store: Store, run: dict) -> dict:
    """Settle a run that was stopped while it was paused.

    A paused run is parked on a row and a session id; no orchestrator is watching
    it, so nobody would ever notice the stop flag. The API invokes this to do the
    settling a live loop would have done -- seal the evidence, land in CANCELLED,
    and start the redirect if the operator sent one.
    """
    fresh = store.get(run["pk"], "META")
    if RunState(fresh["state"]) is not RunState.CANCELLING:
        return {"ok": True, "skipped": f"run is {fresh['state']}"}
    agent = store.try_get(K.agent_pk(fresh["agentId"]), "META") or {"name": fresh["agentId"]}
    _finish(store, fresh, RunState.CANCELLED, "cancelled by you while it was waiting",
            Push(store))
    _chain_redirect(store, fresh["pk"], agent)
    return {"ok": True, "state": RunState.CANCELLED.value}


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
    # Connector tools are not in this list. They are not declared one by one:
    # the model reaches them through connector_search / connector_call, and what
    # it may reach is decided per call from the Bot's grants (see connectors.py).
    resolution = router.resolve_tools(router.ResolutionInput(
        agent_allowed_tools=frozenset(agent.get("allowedTools", [])),
        grants=[],
        connector_covers_outcome=bool(run.get("connectorCoversOutcome")),
    ))

    # --- conversation ------------------------------------------------------
    thread = store.get(K.thread_pk(run["threadId"]), "META")
    # The *newest* MAX_HISTORY rows, oldest first. Asking DynamoDB for `limit=40` in
    # ascending order returns the first 40 ever written, so past 40 rows the model
    # would stop seeing the conversation's end -- including the message it is
    # being asked to answer.
    history = list(reversed(store.query(K.thread_pk(run["threadId"]), sk_prefix="MSG#",
                                        limit=MAX_HISTORY, ascending=False)))
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

    # What a run said before it failed, or before the run it replaced was stopped, is
    # not part of the conversation it is now retrying or answering. A run *resuming*
    # after an approval is different: what it said before pausing is exactly the
    # context it needs, so nothing is left out there. A retry is also sent as a
    # "resume", so telling the two apart is `continuation`'s to say, not something to
    # read off the event here.
    superseded = set() if continuation.is_approval_resume(event) else {
        r for r in (run["runId"], (run.get("trigger") or {}).get("redirectOf")) if r}
    messages = agentcore.build_messages(history, room=thread.get("kind") == "room",
                                        skip_runs=superseded)
    # Decision D4 lives behind `continuation`: how a paused run resumes is the one
    # thing that cannot be verified without AWS, so nothing here knows the shape.
    messages.extend(continuation.resume_messages(event))
    messages = agentcore.end_on_user(messages, run.get("goal", ""))

    # The greeting is stored (so every browser shows the same one) but never sent as
    # a turn; the model is told about it instead. See agentcore.identity_block.
    opening = next((m.get("text", "") for m in history if m.get("starter")), "")
    # The first-conversation script is written for a private chat and is wrong in a
    # group; a room leaves it out (the role and identity still say who this Bot is).
    prompt_agent = agent
    if onboarding.is_brief(agent.get("systemPrompt")):
        # In a room the script is left out. In a private chat the *current* brief is used
        # rather than the copy stored when the Bot was made, which names tools that have
        # since been replaced; a prompt the operator has rewritten is not touched.
        prompt_agent = {**agent, "systemPrompt": "" if thread.get("kind") == "room"
                        else onboarding.brief(agent.get("name") or "Chief")}
    system_prompt = agentcore.build_system_prompt(prompt_agent, memories, skills=assigned_skills,
                                                  opening=opening)
    system_prompt += _request_notes(run, agent, thread)
    system_prompt += _room_note(store, thread, agent, run["threadId"])
    system_prompt += _connected_apps_note(store, run["agentId"])
    system_prompt += _reporting_note(store, agent)


    run = runs.advance(store, run, RunState.PLANNING) if run["state"] == RunState.QUEUED.value else run
    run = runs.advance(store, run, RunState.EXECUTING) if run["state"] == RunState.PLANNING.value else run
    # A retry is a fresh execution attempt. Moving it back through EXECUTING
    # before invoking preserves the state-machine edge and, crucially, lets a
    # second transient startup error return to RETRYING instead of attempting
    # the illegal RETRYING -> RETRYING transition.
    run = runs.advance(store, run, RunState.EXECUTING) if run["state"] == RunState.RETRYING.value else run
    push.state(run["runId"], run["threadId"], run["state"], run.get("costUsd", 0.0))

    # --- stream ------------------------------------------------------------
    core = agentcore.AgentCore()
    parser = StreamParser()
    ev = EvidenceWriter(run["runId"])
    spend = RunCost()
    seq = run.get("cursor", {}).get("lastEventSeq", 0)
    buffer: list[str] = []
    pending_approval: dict | None = None
    stream_error: str | None = None
    turn = Turn()
    started_at = now_iso()

    # The tools this Bot has on this run: the inline ones, plus the optional built-ins the
    # router left it. Sent with every call; see `AgentCore.invoke_stream`.
    call_tools = agentcore.harness_tools(
        [t for t in resolution.tools if t in ("browser", "code_interpreter")])

    # Inline tools the code answered this round, to hand back so the model can go on.
    answered: list[dict] = []
    carried: list[dict] = []
    rounds = 0
    drive_started = time.monotonic()

    def answer(parsed, at_seq: int) -> bool:
        """Handle one tool call. True when the run has to pause for a decision."""
        nonlocal pending_approval
        result = _handle_tool(store, run, agent, ev, push, resolution,
                              parsed, at_seq, spend, turn)
        if result.get("pause"):
            pending_approval = result["approval"]
            return True
        if parsed.tool_name in ROUND_TRIP_TOOLS:
            out = result.get("toolResult", {"ok": True})
            answered.append({"toolUseId": parsed.tool_use_id, "name": parsed.tool_name,
                             "input": parsed.tool_input, "result": out,
                             "error": isinstance(out, dict) and "error" in out})
        return False

    try:
        while True:
            parser = StreamParser()
            answered.clear()
            round_from = len(buffer)
            stream = core.invoke_stream(
                harness_arn=agent["harnessArn"],
                session_id=run["sessionId"],
                messages=messages + carried,
                model_id=model_id,
                system_prompt=system_prompt,
                tools=call_tools,
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

                    if parsed.kind is EventKind.TOOL_USE and answer(parsed, seq):
                        break

                if stream_error or pending_approval:
                    break
                if runs.is_cancelled(store, run):
                    return _settle_cancelled(store, run, agent, push, ev, spend, buffer, turn, started_at)

            if not (stream_error or pending_approval):
                for parsed in parser.flush():
                    if parsed.kind is EventKind.TOOL_USE:
                        seq += 1
                        if answer(parsed, seq):
                            break

            # The model asked for something and stopped to wait for it. Answer, and let it go on.
            if stream_error or pending_approval or not answered:
                break
            if runs.is_cancelled(store, run):
                return _settle_cancelled(store, run, agent, push, ev, spend, buffer, turn, started_at)
            rounds += 1
            out_of_rounds = rounds > MAX_TOOL_ROUNDS
            out_of_time = time.monotonic() - drive_started > ROUND_BUDGET_SECONDS
            if out_of_rounds or out_of_time:
                note = ("\n\n(I stopped here: that was more tool calls than one turn allows. Ask me to carry on.)"
                        if out_of_rounds else
                        "\n\n(I stopped here: that turn ran as long as one turn may. Ask me to carry on.)")
                buffer.append(note)
                push.delta(run["runId"], run["threadId"], note)
                break
            said = "".join(buffer[round_from:])
            carried += continuation.tool_round_messages(said.strip(), list(answered))
            if said.strip():                      # a paragraph break between what it said and what it says next
                buffer.append("\n\n")
                push.delta(run["runId"], run["threadId"], "\n\n")

    except Exception as exc:  # noqa: BLE001
        stream_error = f"{type(exc).__name__}: {exc}"

    text = "".join(buffer).strip()
    if text or turn.steps or turn.cards:
        _persist_message(store, run, agent, text, spend, steps=turn.steps,
                         cards=turn.cards, started_at=started_at)

    run = store.get(run["pk"], "META")
    run = store.update(run["pk"], "META", {
        "cursor": {"turn": run.get("cursor", {}).get("turn", 0) + 1, "lastEventSeq": seq},
        "costUsd": run.get("costUsd", 0.0) + spend.total_usd,
    })
    _write_cost(store, run, agent, spend)

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
            # Kept on the run: only the final attempt's evidence is sealed, so without
            # this the error that *started* a retry chain is gone by the time anyone
            # asks why it failed.
            runs.advance(store, run, RunState.RETRYING,
                         attempt=run.get("attempt", 0) + 1,
                         lastError=stream_error[:500])
            _reinvoke(run["runId"], store.owner_id, delay_note=cls.reason)
            return {"ok": True, "state": RunState.RETRYING.value, "retry": cls.reason}

        _finish(store, run, RunState.FAILED, stream_error, push, ev=ev, cost=spend, text=text)
        return {"ok": False, "state": RunState.FAILED.value}

    # A stop that arrived after the last stream event still has to stop the run:
    # without this a cancelled run would settle as COMPLETED, and a redirect
    # waiting behind it would never be picked up.
    if runs.is_cancelled(store, run):
        return _settle_cancelled(store, run, agent, push, ev, spend, buffer, turn, started_at,
                                 persisted=True)

    _finish(store, run, RunState.COMPLETED, text or "done", push,
            ev=ev, cost=spend, text=text)
    return {"ok": True, "state": RunState.COMPLETED.value}


def _settle_cancelled(store, run, agent, push, ev, cost, buffer, turn, started_at,
                      *, persisted: bool = False) -> dict:
    """A stop, and -- if the operator sent something new while it ran -- the
    redirect that follows it.

    The in-flight action is allowed to finish (the state machine says so); what
    was already said and done is kept, because a run stopped halfway still
    happened and its trail is still evidence.
    """
    text = "".join(buffer).strip()
    if not persisted and (text or turn.steps or turn.cards):
        _persist_message(store, run, agent, text, cost, steps=turn.steps,
                         cards=turn.cards, started_at=started_at)
    _finish(store, run, RunState.CANCELLED,
            "cancelled by you; the in-flight action was allowed to finish",
            push, ev=ev, cost=cost, text=text)
    _chain_redirect(store, run["pk"], agent)
    return {"ok": True, "state": RunState.CANCELLED.value}


def _chain_redirect(store: Store, run_pk: str, agent: dict) -> dict | None:
    """Start the run the operator redirected to, once the old one has stopped.

    One run per thread and agent at a time: two concurrent runs on one session
    would interleave their turns. So a redirect does not start the new run
    beside the old one; it records what to do next on the old run
    (`redirect`, written by the API) and this starts it when the old one is
    really over.
    """
    fresh = store.try_get(run_pk, "META")
    pending = (fresh or {}).get("redirect")
    if not pending or fresh.get("redirectedTo"):
        return None
    carried = {k: v for k, v in (fresh.get("trigger") or {}).items()
               if k in ("skill", "mentions", "woke")}
    new_run = runs.create(store, agent_id=fresh["agentId"], thread_id=fresh["threadId"],
                          goal=pending["text"],
                          trigger={"type": "user", "redirectOf": fresh["runId"], **carried})
    store.update(run_pk, "META", {"redirectedTo": new_run["runId"]})
    threads.event(store, fresh["threadId"],
                  f"Redirected: {agent.get('name', 'the Bot')} stopped and picked up your new message",
                  icon="check")
    _invoke_orchestrator_async(new_run["runId"], store.owner_id)
    return new_run


def _request_notes(run: dict, agent: dict, thread: dict) -> str:
    """What is true of *this* request, appended to the system prompt.

    Behaviour, not enforcement: it tells the model why it was woken. The skill it
    was told to use was already checked against the Bot's assignments by the API
    before the run existed, and a handoff it is nudged toward still goes through
    the handoff machinery -- which is what actually gates it.
    """
    trig = run.get("trigger") or {}
    notes = []
    skill = trig.get("skill")
    if skill:
        notes.append(f'The operator invoked the skill "{skill.get("name")}" for this '
                     "request. Follow it.")
    woke = [w for w in (trig.get("woke") or []) if w]
    if thread.get("kind") == "room" and len(woke) > 1:
        notes.append("Several Bots were woken by this message (" + ", ".join(woke) + "). "
                     "Answer only for your own lane; do not repeat what another will cover.")
    mentions = [m for m in (trig.get("mentions") or []) if m and m != agent["agentId"]]
    if mentions and thread.get("kind") != "room":
        notes.append("The operator mentioned " + ", ".join("@" + m for m in mentions)
                     + ". If that Bot owns this work, use the handoff tool to give it "
                     "to them rather than doing it yourself.")
    if not notes:
        return ""
    return "\n\n## This request\n" + "\n".join(f"- {n}" for n in notes)


def _room_note(store: Store, thread: dict, agent: dict, thread_id: str) -> str:
    """Where this Bot is, and who is with it, when the thread is a group chat.

    Without this a Bot in a room knows nothing of the others beyond whatever they
    happened to say, so it answers for them ("Chief handles inbox and calendar...")
    instead of letting them speak -- and it cannot pull one in, because
    `message_agent` needs the room's id and nothing ever told it. Guidance, not
    enforcement: `collab.send` is what actually checks who may message whom.
    """
    if thread.get("kind") != "room":
        return ""
    me = agent["agentId"]
    mates = []
    for aid in thread.get("agentIds") or []:
        if aid == me:
            continue
        row = store.try_get(K.agent_pk(aid), "META")
        if not row or row.get("status") not in A.RUNNABLE:
            continue
        detail = ", ".join(b for b in (row.get("title"), row.get("role")) if b)
        mates.append(f"- {row.get('name', aid)} (@{aid})" + (f": {detail}" if detail else ""))
    title = thread.get("title") or "this room"
    who = ("the operator and:\n" + "\n".join(mates)) if mates else "the operator"
    return (
        f'\n\n## This room\nYou are in a group chat, "{title}", with {who}\n'
        "Everyone here sees every message.\n"
        "- Speak only for yourself. Never answer on a teammate's behalf, and never "
        "describe what a teammate does or will do as though they had said it. If a "
        "question is about them, or the job is theirs, bring them in: call "
        f'message_agent with `to` set to their id, `collaboration_context_id` "{thread_id}", '
        "`priority` true and a plain request, then tell the operator you asked them.\n"
        "- If you have already introduced yourself here, do not do it again. When "
        "greeted, one short line: your name, your role, what you can take on.\n"
        "- This is not a private chat: do not run a first-conversation menu. Keep "
        "replies short unless asked for more. If a teammate has already said what "
        "you would, add only what is new, or say nothing.\n"
        "- When the operator gives this room a task, begin work immediately: assess "
        "the task from your own specialty, take one concrete low-risk lane, and "
        "surface a gap or dependency to the relevant teammate with `message_agent` "
        "when needed. Do not wait for a lead to assign you. For an outside action "
        "that changes data, spends money, or has another consequence, use the tool "
        "that routes it for operator approval; do not merely say that approval is needed."
    )


def _connected_apps_note(store: Store, agent_id: str) -> str:
    """Which apps this Bot holds, so it knows to look before it says it cannot.

    Guidance, not enforcement: naming an app here grants nothing, and leaving one
    out hides nothing that connector_search would not also hide.
    """
    apps = sorted({g.slug for g in connectors.granted_apps(store, agent_id)})
    if not apps:
        return ""
    return ("\n\n## Connected apps\n"
            f"You have access to: {', '.join(apps)}. Use connector_search to find what "
            "you can do in them, then connector_call to do it. Reading runs at once; "
            "anything that creates, changes or removes data waits for the operator's "
            "approval. For an app not listed, use request_connector.")


def _reporting_note(store: Store, agent: dict) -> str:
    """Who this Bot works under and who works under it.

    Context, not authority, and it says so: a Bot that believed seniority let it
    approve a teammate's action would be wrong, and nothing here would make it
    right. The line only tells a Bot where to escalate and whom to keep informed.
    """
    rows = store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
    manager = org.resolve(rows)
    if agent["agentId"] not in manager:
        return ""
    names = {r["agentId"]: r.get("name", r["agentId"]) for r in rows}
    boss = manager[agent["agentId"]]
    team = [names[a] for a in org.reports_of(agent["agentId"], manager)]

    lines = ["\n\n## Who you report to",
             f"You report to {names[boss]}." if boss
             else "You report directly to the operator."]
    if team:
        lines.append(f"Reporting to you: {', '.join(team)}.")
    lines.append("This is how the team is organised, so you know who to bring a blocker to "
                 "and who to keep informed. It changes nothing about what anyone may do: "
                 "approvals come from the operator, and no Bot approves another Bot's actions.")
    return "\n".join(lines)


def _handle_tool(store, run, agent, ev, push, resolution, parsed, seq, cost,
                 turn: Turn | None = None) -> dict:
    """Answer an inline function call, or record an in-harness tool call.

    Every branch ends by recording a step with Auto Review's verdict on it
    (`review`): allowed, asked or denied, and by which rule. The verdicts are
    *descriptions of decisions made here* -- by `policy`, by `router`, by a
    matched approval -- never a second opinion that could disagree with the gate
    it labels.
    """
    turn = turn if turn is not None else Turn()
    name = parsed.tool_name
    args = parsed.tool_input
    preapproved = frozenset(agent.get("preapproved", []))

    if name in ("create_agent", "propose_agent"):
        return _create_agent_tool(store, run, agent, ev, push, turn, parsed, seq, args)

    if name == "update_agent":
        return _update_agent_tool(store, run, agent, ev, push, turn, seq, args)

    if name == "create_group_chat":
        try:
            result = _create_group_chat(store, run, agent, args)
        except ValueError as exc:
            ev.error(seq, "terminal", str(exc))
            _step(push, run, turn, "group_chat.create", f"not started: {exc}",
                  review.Review(review.DENIED, "collab", str(exc)))
            return {"pause": False, "toolResult": {"error": str(exc)}}
        ev.action(seq, "group_chat.create", f"opened {result['title']}",
                  threadId=result["threadId"])
        _step(push, run, turn, "group_chat.create",
              f"started {result['title']} with {len(result['agentIds'])} Bots",
              review.scoped("collab", "internal task room; each Bot keeps its own access and approvals"))
        return {"pause": False, "toolResult": {
            "created": True, "threadId": result["threadId"], "agentIds": result["agentIds"],
            "note": "every participant was started on the shared goal in its own run"}}

    if name == "find_agents":
        matches = _find_agents(store, args.get("query", ""))
        _step(push, run, turn, "agent.find", f"found {len(matches)} active Bots",
              review.scoped("roster", "names and roles only; no access or approval authority travels"))
        return {"pause": False, "toolResult": {"agents": matches}}

    if name == "request_approval":
        action = args.get("action", "unknown")
        arguments = args.get("arguments", {}) or {}
        capability = _capability_for(action, agent)

        try:
            decision = policy.evaluate(action, capability, preapproved=preapproved)
        except policy.Refused as exc:
            ev.error(seq, "terminal", str(exc))
            _step(push, run, turn, action, f"refused: {exc}", review.refused(exc))
            return {"pause": False, "toolResult": {"error": f"refused: {exc}"}}

        if not decision.required:
            # Already covered; tell the agent to proceed rather than pausing.
            _step(push, run, turn, action, f"pre-approved ({decision.reason})",
                  review.from_decision(decision))
            return {"pause": False, "toolResult": {
                "decision": "pre-approved", "proceed": True, "reason": decision.reason}}

        approval = approvals.request(
            store, run,
            action=action, arguments=arguments,
            why=args.get("why", ""), capability=capability,
            tool_use_id=parsed.tool_use_id, tool_name=name, tool_input=args,
            target=args.get("target") or {},
            reversible=args.get("reversible"), decision=decision,
        )
        ev.action(seq, action, "approval requested", approvalId=approval["approvalId"])
        _step(push, run, turn, action, "waiting for your approval", review.from_decision(decision))
        return {"pause": True, "approval": approval}

    if name == "handoff":
        handoff = _record_handoff(store, run, args)
        ev.action(seq, "handoff", f"to {args.get('to')}", handoffId=handoff["handoffId"])
        push.handoff(run["runId"], run["threadId"], handoff)
        _step(push, run, turn, "handoff", f"to {args.get('to')}",
              review.scoped("handoff", "you stay the owner, and no access travels with it"))
        return {"pause": False, "toolResult": {
            "ok": True, "handoffId": handoff["handoffId"],
            "note": "recorded and shown to the operator; no access travels with a handoff"}}

    if name == "message_agent":
        try:
            result = _message_agent(store, run, agent, args)
        except collab.MessagingError as exc:
            ev.error(seq, "terminal", str(exc))
            _step(push, run, turn, "message_agent", f"blocked: {exc}",
                  review.Review(review.DENIED, "collab", str(exc)))
            return {"pause": False, "toolResult": {"error": str(exc)}}
        ev.action(seq, "message_agent", f"to {args.get('to')}"
                 f" ({'priority' if result['priorityGranted'] else 'deferred'})",
                 messageId=result["message"]["messageId"])
        _step(push, run, turn, "message_agent",
              f"-> {args.get('to')}: {args.get('text','')[:120]}",
              review.scoped("collab", "bound to this task; the recipient's own limits still apply"))
        return {"pause": False, "toolResult": {
            "delivered": True, "woke": result["woke"],
            "note": "the recipient will answer in their own turn; do not wait for it here"}}

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
            _step(push, run, turn, "remember", f"not saved: {exc}",
                  review.Review(review.DENIED, "memory", str(exc)))
            return {"pause": False, "toolResult": {"error": str(exc)}}
        store.put(row)
        ev.action(seq, "remember", f"{scope} memory saved", memId=row["memId"])
        label = (row.get("title") or row.get("body") or "")[:80]
        _step(push, run, turn, "remember", f"saved: {label}",
              review.scoped("memory", "writes only to this Bot's own memory"))
        # A real event, written by the action itself -- not something the
        # console noticed. It is what lets the operator see the save happened.
        threads.event(store, run["threadId"], f"{agent['name']} saved to memory: {label}",
                      icon="layers", memId=row["memId"])
        return {"pause": False, "toolResult": {"saved": True, "memId": row["memId"]}}

    if name == "propose_shared_memory":
        # Same pattern as propose_agent/propose_skill: the model nominates, a
        # person decides. Nothing here reaches shared_user until _decide
        # approves it -- see memory.plan_write's status="proposed" default.
        try:
            fields = memory.validate(args, scope="shared_user")
        except memory.ValidationError as exc:
            ev.error(seq, "terminal", str(exc))
            _step(push, run, turn, "memory.publish", f"not proposed: {exc}",
                  review.Review(review.DENIED, "memory", str(exc)))
            return {"pause": False, "toolResult": {"error": str(exc)}}
        proposal = {**fields, "proposedBy": agent["agentId"]}
        decision = policy.evaluate("memory.publish", Capability.WRITE, preapproved=preapproved)
        approval = approvals.request(
            store, run,
            action="memory.publish", arguments=proposal,
            why=args.get("why", "The operator should know this."),
            capability=Capability.WRITE,
            tool_use_id=parsed.tool_use_id, tool_name=name, tool_input=args,
            reversible=True, decision=decision,
        )
        ev.action(seq, "memory.publish", "shared memory proposed", approvalId=approval["approvalId"])
        _step(push, run, turn, "memory.publish", "proposed for every Bot",
              review.from_decision(decision))
        return {"pause": True, "approval": approval}

    if name == "propose_skill":
        # Same pattern as propose_agent: the model nominates, a person
        # decides. A proposed skill is excluded from every agent's context
        # until approved and assigned -- see skills.assigned_active_skills.
        try:
            proposal = skills.validate_skill(args)
        except skills.ValidationError as exc:
            ev.error(seq, "terminal", str(exc))
            _step(push, run, turn, "skill.create", f"not proposed: {exc}",
                  review.Review(review.DENIED, "skill", str(exc)))
            return {"pause": False, "toolResult": {"error": str(exc)}}
        proposal["proposedBy"] = agent["agentId"]
        decision = policy.evaluate("skill.create", Capability.ADMIN)
        approval = approvals.request(
            store, run,
            action="skill.create", arguments=proposal,
            why=args.get("why", "A reusable skill would help future runs."),
            capability=Capability.ADMIN,
            tool_use_id=parsed.tool_use_id, tool_name=name, tool_input=args,
            reversible=True, decision=decision,
        )
        ev.action(seq, "skill.create", "skill proposed", approvalId=approval["approvalId"])
        _step(push, run, turn, "skill.create", f"proposed {proposal['name']}",
              review.from_decision(decision))
        return {"pause": True, "approval": approval}

    # --- proposals that are cards, not approvals -----------------------------
    # Nothing here changes anything. A card is a suggestion the operator acts on
    # with the console's own forms, so there is no approval to wait for and the
    # run carries on -- and because it carries on, the model is told to make
    # these its last action, after the result they follow.
    if name == "request_connector":
        try:
            slug = connectors.slug_of(args.get("connectorId") or "")
            app = _composio_client().toolkit(slug)
        except (connectors.UnknownConnector, composio.ComposioError) as exc:
            ev.error(seq, "terminal", f"request_connector: {exc}")
            _step(push, run, turn, "request_connector", f"no such app {args.get('connectorId')!r}",
                  review.Review(review.DENIED, "unknown_connector",
                                "that app is not one Composio offers", str(args.get("connectorId", ""))))
            return {"pause": False, "toolResult": {"error": "that app is not available"}}
        cid = connectors.connector_id(slug)
        if any(g.connector_id == cid for g in connectors.granted_apps(store, run["agentId"])):
            _step(push, run, turn, "request_connector", f"{app['name']} is already connected",
                  review.scoped("proposal", "already connected and granted; nothing to ask for"))
            return {"pause": False, "toolResult": {
                "already_connected": True,
                "note": f"{app['name']} is already connected; use connector_search to find what you can do in it"}}
        turn.cards.append({"type": "connect", "connectorId": cid, "name": app["name"],
                           "why": (args.get("why") or "")[:200]})
        ev.action(seq, "request_connector", f"asked to connect {app['name']}")
        _step(push, run, turn, "request_connector", f"asked you to connect {app['name']}",
              review.scoped("proposal", "a suggestion; nothing is connected until you do it"))
        return {"pause": False, "toolResult": {
            "ok": True,
            "note": f"the operator was shown a card to connect {app['name']}; say what it is for and stop"}}

    if name == "propose_routine":
        try:
            proposal = routines.validate_proposal(args)
        except routines.ValidationError as exc:
            ev.error(seq, "terminal", str(exc))
            _step(push, run, turn, "propose_routine", f"not proposed: {exc}",
                  review.Review(review.DENIED, "routine", str(exc)))
            return {"pause": False, "toolResult": {"error": str(exc)}}
        turn.cards.append({"type": "routine", **proposal, "agentId": agent["agentId"]})
        ev.action(seq, "propose_routine", f"proposed {proposal['name']}")
        _step(push, run, turn, "propose_routine", f"suggested {proposal['name']}",
              review.scoped("proposal", "a suggestion; you review it before it exists"))
        return {"pause": False, "toolResult": {
            "ok": True, "note": "the operator was shown the routine to review; nothing runs until they set it up"}}

    # --- connector tools ------------------------------------------------------
    if name == "connector_search":
        return _connector_search(store, run, ev, push, turn, seq, args)
    if name == "connector_call":
        return _connector_call(store, run, ev, push, turn, parsed, seq, cost, args, preapproved)

    # --- in-harness tool --------------------------------------------------------
    if not router.may_call(name, resolution):
        # Should be unreachable: unresolved tools are absent from the schema.
        ev.error(seq, "terminal", f"{name} called without a grant")
        _step(push, run, turn, name, "no grant", review.ungranted(name))
        return {"pause": False}

    safe_args, redacted = redact.redact(args)
    summary = _summarise(name, safe_args)
    verdict = review.sandbox(name)
    runs.record_event(store, run, seq, "tool", tool=name, args=safe_args,
                      redactions=redacted, review=verdict.to_dict())
    ev.action(seq, name, summary, redactions=redacted, review=verdict.to_dict())
    cost.add_connector(name.split(".")[0], calls=1)
    _step(push, run, turn, name, summary, verdict)
    store.update(run["pk"], "META",
                 {"toolCallCount": run.get("toolCallCount", 0) + 1})
    return {"pause": False}


#: Words a model can act on, in place of an enum it would have to look up.
_EFFECT = {"read": "reads only", "write": "changes data", "cost": "spends money",
           "destructive": "removes data", "admin": "changes settings"}


def _compact_schema(schema: dict) -> dict:
    """A tool's inputs, small enough to put in front of a model many at a time."""
    props = (schema or {}).get("properties") or {}
    out = {}
    for key, spec in list(props.items())[:14]:
        kind = spec.get("type", "any") if isinstance(spec, dict) else "any"
        text = (spec.get("description") or "") if isinstance(spec, dict) else ""
        out[key] = f"{kind} - {text[:90]}" if text else str(kind)
    return {"required": [r for r in ((schema or {}).get("required") or []) if r in props][:14],
            "properties": out}


def _connector_search(store, run, ev, push, turn, seq, args) -> dict:
    """List what this Bot may do in the apps it holds. Runs nothing.

    What comes back is already narrowed by the grant: an app the Bot was not
    given, and a tool above its ceiling, are not shown -- absent, not refused.
    """
    query = (args.get("query") or "").strip()[:200]
    only = (args.get("app") or "").strip().lower().removeprefix(connectors.PREFIX)
    verdict = review.scoped("connector", "lists what this Bot may use; runs nothing")

    granted = [g for g in connectors.granted_apps(store, run["agentId"])
               if not only or g.slug == only]
    if not granted:
        _step(push, run, turn, "connector_search", "no connected app matches", verdict)
        return {"pause": False, "toolResult": {
            "tools": [],
            "note": "No connected app matches. Say what you can do without one, and "
                    "use request_connector if the operator should connect it."}}

    client = _composio_client()
    found: list[dict] = []
    try:
        for g in granted[:8]:
            for t in client.tools(g.slug, query=query or None, limit=6):
                capability = Capability(t["capability"])
                try:
                    connectors.authorize([g], toolkit_slug=g.slug, tool=t["tool"],
                                         capability=capability)
                except connectors.NotGranted:
                    continue
                found.append({"tool": t["tool"], "app": g.slug,
                              "does": t["description"][:240],
                              "effect": _EFFECT.get(capability.value, capability.value),
                              "inputs": _compact_schema(t["inputSchema"])})
    except composio.ComposioError as exc:
        ev.error(seq, "retryable", f"connector_search failed: {exc}")
        _step(push, run, turn, "connector_search", "could not search", verdict)
        return {"pause": False, "toolResult": {"error": f"search failed: {exc}"}}

    apps = ", ".join(sorted({g.slug for g in granted}))
    ev.action(seq, "connector_search", f"searched {apps}: {query}"[:160])
    _step(push, run, turn, "connector_search", f"searched {apps}"[:160], verdict)
    return {"pause": False, "toolResult": {"tools": found[:20]}}


def _connector_call(store, run, ev, push, turn, parsed, seq, cost, args, preapproved) -> dict:
    """Run one Composio tool for a Bot -- the gate, then the call.

    The order is the point. The tool is looked up and classified from Composio's
    own tags (the model does not get to say what it does), checked against the
    Bot's grant, and put through `policy.evaluate`; only a call that survives all
    three reaches Composio. Enforcement is code, never prompt: a Bot that skips
    `request_approval` and calls a write directly is stopped here all the same.
    """
    slug = (args.get("tool") or "").strip()
    arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
    client = _composio_client()

    try:
        meta = client.tool(slug)
        capability = Capability(meta["capability"])
        grant = connectors.authorize(connectors.granted_apps(store, run["agentId"]),
                                     toolkit_slug=meta["toolkit"], tool=slug,
                                     capability=capability)
        decision = policy.evaluate(slug, capability, preapproved=preapproved)
    except policy.Refused as exc:
        ev.error(seq, "terminal", str(exc))
        _step(push, run, turn, slug, f"refused: {exc}", review.refused(exc))
        return {"pause": False, "toolResult": {"error": str(exc)}}
    except connectors.NotGranted as exc:
        ev.error(seq, "terminal", str(exc))
        _step(push, run, turn, slug or "connector_call", f"not allowed: {exc}",
              review.Review(review.DENIED, "no_grant", str(exc), slug))
        return {"pause": False, "toolResult": {"error": str(exc)}}
    except composio.ComposioError as exc:
        ev.error(seq, "retryable", f"{slug or 'connector_call'} lookup failed: {exc}")
        _step(push, run, turn, slug or "connector_call", "could not look that tool up",
              review.ungranted(slug or "that tool"))
        return {"pause": False, "toolResult": {"error": f"could not look up {slug}: {exc}"}}

    verdict = review.from_decision(decision)
    if decision.required:
        held = approvals.find_grant(store, run["pk"], slug, arguments)
        if held and approvals.consume(store, held):
            verdict = review.approved(held["approvalId"])
        else:
            approval = approvals.request(
                store, run, action=slug, arguments=arguments,
                why=f"{slug} needs your approval ({decision.reason})",
                capability=capability, tool_use_id=parsed.tool_use_id,
                tool_name="connector_call", tool_input=args,
                target={"app": grant.slug}, reversible=None, decision=decision,
            )
            ev.action(seq, slug, "held for approval before running",
                      approvalId=approval["approvalId"])
            _step(push, run, turn, slug, "waiting for your approval", verdict)
            return {"pause": True, "approval": approval}

    safe_args, redacted = redact.redact(arguments)
    summary = f"{slug} {json.dumps(safe_args, default=str)}"[:160]
    runs.record_event(store, run, seq, "tool", tool=slug, args=safe_args,
                      redactions=redacted, review=verdict.to_dict())
    ev.action(seq, slug, summary, redactions=redacted, review=verdict.to_dict())
    cost.add_connector(grant.slug, calls=1)
    _step(push, run, turn, slug, summary, verdict)

    try:
        # Read the grants again here rather than reusing the ones above: a revoke
        # that lands mid-run is honoured on the very next call, not the next run.
        grant = connectors.authorize(connectors.granted_apps(store, run["agentId"]),
                                     toolkit_slug=meta["toolkit"], tool=slug,
                                     capability=capability)
        result = connectors.invoke(store, client, agent_id=run["agentId"], grant=grant,
                                   tool=slug, arguments=arguments, run_id=run["runId"])
    except Exception as exc:  # noqa: BLE001
        ev.error(seq, "retryable", f"{slug} failed: {type(exc).__name__}")
        return {"pause": False, "toolResult": {"error": f"{slug} failed: {exc}"}}
    return {"pause": False, "toolResult": redact.redact(result)[0]}


def _owner_asked(run: dict) -> bool:
    """Whether the operator's own message started this run.

    Read from how the run was created, in code, and never from anything the model
    said: a routine, a teammate's message or background work is not the operator
    asking. It is what lets a Bot make a Bot without a card -- and only then.
    """
    return (run.get("trigger") or {}).get("type") == "user"


def _create_agent_tool(store, run, agent, ev, push, turn, parsed, seq, args) -> dict:
    """`create_agent`: make a Bot when the operator asked for one, otherwise ask them.

    When the operator's own message started this run, the Bot exists at once (see
    `provisioning.create_child` for what it inherits and what it does not). Any other
    run is a Bot acting on its own initiative, and that still ends in a card the
    operator approves: an instruction planted in something a Bot read must not be
    able to create Bots. The model is told which happened.
    """
    if not _owner_asked(run):
        return _propose_agent(store, run, agent, ev, push, turn, parsed, seq, args)

    try:
        child = provisioning.create_child(store, agent, args)
    except provisioning.ChildCreationError as exc:
        ev.error(seq, "terminal", f"create_agent: {exc}")
        _step(push, run, turn, "agent.create", f"not created: {exc}",
              review.Review(review.DENIED, "agent", str(exc)))
        return {"pause": False, "toolResult": {"error": str(exc)}}

    ev.action(seq, "agent.created", f"created {child['agentId']}", agentId=child["agentId"])
    _step(push, run, turn, "agent.created", f"created {child['name']}",
          review.scoped("agent", f"you asked for it; it reports to {agent['name']} and holds no more "
                                 "access than they do"))
    threads.event(store, run["threadId"], f"{agent['name']} created {child['name']}",
                  icon="check", agentId=child["agentId"])
    push.notification("info", f"{agent['name']} created {child['name']}")

    briefed = _brief_child(store, run, agent, child, args.get("firstTask"))
    return {"pause": False, "toolResult": {
        "created": True, "agentId": child["agentId"], "name": child["name"],
        "reportsTo": agent["agentId"], **briefed,
        "note": ("It is in the operator's roster now. Tell them briefly what you made; "
                 "do not wait for it here.")}}


def _propose_agent(store, run, agent, ev, push, turn, parsed, seq, args) -> dict:
    """A Bot's own idea for a new Bot: shown to the operator to approve, never created."""
    proposal = _agent_creation_proposal(args, parent_agent_id=agent["agentId"])
    try:
        profile = A.validate_profile(proposal)
    except A.ValidationError as exc:
        ev.error(seq, "terminal", f"create_agent: {exc}")
        _step(push, run, turn, "agent.create", f"not proposed: {exc}",
              review.Review(review.DENIED, "agent", str(exc)))
        return {"pause": False, "toolResult": {"error": str(exc)}}
    # Carry the *normalized* profile onto the card, not the raw arguments. The
    # return value used to be discarded, so a name the validator had tidied --
    # a title lifted out of it, an em dash folded to a hyphen -- was approved
    # in its original form and the tidying was silently undone.
    proposal.update({k: profile[k] for k in ("name", "title", "role", "description")})
    # The always-approve floor includes agent.create. Calling the central
    # policy gate here keeps that invariant explicit if the policy evolves.
    decision = policy.evaluate("agent.create", Capability.ADMIN)
    approval = approvals.request(
        store, run,
        action="agent.create", arguments=proposal,
        why=args.get("why", "A separate companion is needed for this lane."),
        capability=Capability.ADMIN,
        tool_use_id=parsed.tool_use_id, tool_name=parsed.tool_name, tool_input=args,
        target={"parentAgentId": agent["agentId"]},
        reversible=False, decision=decision,
    )
    ev.action(seq, "agent.create", "agent creation proposed", approvalId=approval["approvalId"])
    _step(push, run, turn, "agent.create", f"proposed {proposal['name']}",
          review.from_decision(decision))
    return {"pause": True, "approval": approval}


def _brief_child(store, run, creator: dict, child: dict, task) -> dict:
    """Give a new Bot its first job, and wake it to start on it.

    The job is the run's goal, so it is what the new Bot is asked; the transcript
    gets one line saying who briefed whom, on both Bots' threads. It goes through
    the same wake gate any priority message does (`collab.may_wake_now`: the Bot's own
    concurrency and budget), so a Bot that cannot start yet is told so instead of
    being started anyway.
    """
    task = (task or "").strip() if isinstance(task, str) else ""
    if not task:
        return {"briefed": False, "briefing": "no firstTask was given, so it is waiting for one"}
    ok, why = collab.may_wake_now(store, child, collab.limits_for_org(store))
    if not ok:
        return {"briefed": False, "briefing": f"it could not start yet: {why}"}
    thread_id = f"dm-{child['agentId']}"
    new_run = runs.create(store, agent_id=child["agentId"], thread_id=thread_id, goal=task,
                          trigger={"type": "agent", "fromAgentId": creator["agentId"], "brief": True})
    threads.event(store, thread_id,
                  f"{creator['name']} briefed {child['name']}: {task[:240]}", icon="task",
                  fromAgentId=creator["agentId"])
    _invoke_orchestrator_async(new_run["runId"], store.owner_id)
    return {"briefed": True, "runId": new_run["runId"]}


#: What a Bot may refine about a Bot it made. Not access, budget, tools or status.
_REFINABLE = ("name", "title", "role", "description")


def _update_agent_tool(store, run, agent, ev, push, turn, seq, args) -> dict:
    """`update_agent`: refine a Bot this Bot created, when the operator asked.

    Names, titles, roles and standing orders only, through the same `plan_update` a
    person's edit goes through, so it is validated and audited the same way. The
    fields that carry authority are privileged there and an agent is refused them.
    """
    def refuse(why: str) -> dict:
        ev.error(seq, "terminal", f"update_agent: {why}")
        _step(push, run, turn, "agent.update", f"not updated: {why}",
              review.Review(review.DENIED, "agent", why))
        return {"pause": False, "toolResult": {"error": why}}

    if not _owner_asked(run):
        return refuse("this only works when the operator's own message started the turn; "
                      "ask them for the change instead")
    target_id = (args.get("agentId") or "").strip()
    target = store.try_get(K.agent_pk(target_id), "META") if target_id else None
    if not target or target.get("parentAgentId") != agent["agentId"]:
        return refuse("you can only refine a Bot you created")
    body = {k: args[k].strip() for k in _REFINABLE
            if isinstance(args.get(k), str) and args[k].strip()}
    if not body:
        return refuse("nothing to change: pass at least one of " + ", ".join(_REFINABLE))
    actor = A.Actor(user_id=store.owner_id, org_id=agent.get("orgId") or "", agent_id=agent["agentId"])
    try:
        changes, events = A.plan_update(target, body, actor)
    except (A.ValidationError, A.Escalation) as exc:
        return refuse(str(exc))
    store.update(K.agent_pk(target_id), "META", changes)
    for event_row in events:
        store.put(event_row)
    ev.action(seq, "agent.update", f"refined {target_id}", agentId=target_id)
    _step(push, run, turn, "agent.update", f"refined {target['name']}",
          review.scoped("agent", "a Bot you created; name, title, role and standing orders only"))
    return {"pause": False, "toolResult": {"updated": sorted(body), "agentId": target_id}}


def _agent_creation_proposal(args: dict, *, parent_agent_id: str) -> dict:
    """Normalize the only fields a model may nominate for a child agent.

    These limits are intentionally below the normal human Create-a-Bot
    defaults. A newly approved companion has a useful, bounded first session;
    granting connectors, optional computer tools, or a larger budget remains a
    distinct owner action in the console.
    """
    return {
        "name": args.get("name", ""),
        "title": args.get("title", ""),
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


_composio = None


def _composio_client():
    """Built on first use, so a run that calls no connector never reads the
    Composio secret."""
    global _composio
    if _composio is None:
        _composio = composio.Composio()
    return _composio


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


def _create_group_chat(store: Store, run: dict, agent: dict, args: dict) -> dict:
    """Open a task room from a Bot turn and start its collaborators.

    A room is internal coordination, not a new authority boundary: every Bot keeps
    its own grants, limits and approval rules. The initiating Bot is always included
    so it cannot create an unobserved conversation for other Bots, and every member
    receives the same concrete goal in its own thread session.
    """
    title = (args.get("title") or "").strip()
    if not 2 <= len(title) <= 80:
        raise ValueError("a group-chat title must be 2-80 characters")
    goal = (args.get("goal") or "").strip()
    if not goal:
        raise ValueError("create_group_chat requires a concrete goal")
    invited = args.get("agentIds")
    if not isinstance(invited, list) or not all(isinstance(a, str) and a.strip() for a in invited):
        raise ValueError("agentIds must be a list of Bot ids")

    agent_ids = list(dict.fromkeys([agent["agentId"], *(a.strip() for a in invited)]))
    if len(agent_ids) < 2:
        raise ValueError("a group chat needs at least one other active Bot")
    if len(agent_ids) > collab.MAX_ROOM_MEMBERS:
        raise ValueError(f"a group chat holds at most {collab.MAX_ROOM_MEMBERS} Bots")

    members = []
    for agent_id in agent_ids:
        member = store.try_get(K.agent_pk(agent_id), "META")
        if not member or member.get("status") not in A.RUNNABLE:
            raise ValueError(f"no such active Bot {agent_id!r}")
        members.append(member)

    thread_id = new_id("th_")
    store.put({
        "pk": K.thread_pk(thread_id), "sk": "META",
        "entity": "Thread", "threadId": thread_id,
        "gsi1pk": "THREADS", "gsi1sk": now_iso(),
        "kind": "room", "title": title, "agentIds": agent_ids,
        "sessionId": K.session_id(thread_id), "lastActivity": now_iso(),
        "createdBy": f"agent:{agent['agentId']}", "openedFromRunId": run["runId"],
        "status": "active",
    })
    threads.event(store, thread_id, f"{agent['name']} opened this group chat: {goal}",
                  icon="check", fromAgentId=agent["agentId"])

    names = [member.get("name", member["agentId"]) for member in members]
    for member in members:
        started = runs.create(
            store, agent_id=member["agentId"], thread_id=thread_id, goal=goal,
            trigger={"type": "group_chat", "fromAgentId": agent["agentId"],
                     "woke": names, "collaborationContextId": thread_id},
        )
        _invoke_orchestrator_async(started["runId"], store.owner_id)

    return {"threadId": thread_id, "title": title, "agentIds": agent_ids}


def _find_agents(store: Store, query: str) -> list[dict]:
    """The small, read-only roster slice a Bot needs to form a task room."""
    needle = " ".join((query or "").lower().split())
    words = needle.split()
    rows = store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
    matches = []
    for row in rows:
        if row.get("status", row.get("state")) not in A.RUNNABLE:
            continue
        haystack = " ".join(str(row.get(k) or "") for k in ("agentId", "name", "title", "role")).lower()
        if words and not all(word in haystack for word in words):
            continue
        matches.append({key: row[key] for key in ("agentId", "name") if key in row} | {
            "title": row.get("title", ""), "role": row.get("role", "")})
    return matches[:20]


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


def _persist_message(store: Store, run: dict, agent: dict, text: str, cost: RunCost,
                     *, steps: list | None = None, cards: list | None = None,
                     started_at: str | None = None) -> None:
    """Write what this turn produced: words, the trail behind them, and any cards.

    A turn can have steps and cards and no words (it paused on an approval
    before saying anything), so the row is written for any of the three;
    `build_messages` skips a row with no text, so the model never sees the empty
    ones.
    """
    row = {
        "pk": K.thread_pk(run["threadId"]),
        "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "assistant",
        "author": agent.get("name"), "agentId": agent["agentId"],
        "runId": run["runId"], "text": text,
        "usage": cost.to_item(),
    }
    if steps:
        row["steps"] = steps
        row["startedAt"] = started_at or steps[0]["at"]
        row["endedAt"] = now_iso()
    if cards:
        row["cards"] = cards
    store.put(row)
    if not text:
        return
    # A reply is activity. Without this the thread's `lastActivity` stays at
    # the operator's own message, so a Bot answering after the thread was
    # opened could never make it unread. Cosmetic to the run, so a failure
    # here is logged and never allowed to fail the run that just succeeded.
    try:
        store.update(K.thread_pk(run["threadId"]), "META", threads.touch(text, "assistant"))
    except Exception:  # noqa: BLE001
        traceback.print_exc()


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
