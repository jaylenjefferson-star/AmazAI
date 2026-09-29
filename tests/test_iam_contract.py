"""What the code calls in AWS is what the stack grants.

Twice a call was denied for an action that was not the one it is named after, and nothing
failed loudly because the code swallowed the denial (a failed tool sync must not stop a
run). `CreateHarness` is authorised as `CreateAgentRuntime` (the stack says so in a
comment). `UpdateHarness` was granted by name and still denied: AWS evaluated it as
`bedrock-agentcore:UpdateAgentRuntime` on the harness's runtime. Current
`CreateHarness` also evaluates Memory and tagging dependencies. A guard that
only checked same-named actions passed both times.

So this reads the control-plane and runtime calls `agentcore.py` makes, expands each to
every action AWS is known to evaluate it as, and checks the stack grants all of them.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACK = (ROOT / "infra" / "lib" / "amazai-stack.ts").read_text()
AGENTCORE = (ROOT / "services" / "amazai" / "agentcore.py").read_text()

#: What AWS actually evaluates a call as. Add to this the next time a denial names an action
#: the call is not called; the AccessDeniedException text says which.
AUTHORISED_AS = {
    "create_harness": {
        "CreateHarness", "CreateAgentRuntime", "CreateMemory", "GetMemory", "TagResource",
        "CreateHarnessEndpoint", "CreateAgentRuntimeEndpoint",
    },
    "update_harness": {"UpdateHarness", "UpdateAgentRuntime", "UpdateAgentRuntimeEndpoint"},
}


def pascal(snake: str) -> str:
    return "".join(part.title() for part in snake.split("_"))


def statement_for(function: str, sid: str) -> str:
    """The source of `<function>.addToRolePolicy(... sid: '<sid>' ...)`."""
    m = re.search(rf"{function}\.addToRolePolicy\(new iam\.PolicyStatement\(\{{\s*sid: '{sid}'.*?\}}\)\);",
                  STACK, re.S)
    assert m, f"{function} has no policy statement {sid!r}"
    return m.group(0)


def granted() -> set[str]:
    return set(re.findall(r"'bedrock-agentcore:(\w+)'", STACK))


def test_every_aws_call_the_code_makes_is_granted_including_what_aws_evaluates_it_as():
    calls = set(re.findall(r"self\._(?:control|runtime)\.(\w+)\(", AGENTCORE))
    assert {"create_harness", "invoke_harness"} <= calls, calls
    needed = set().union(*(AUTHORISED_AS.get(c, {pascal(c)}) for c in calls))
    missing = sorted(needed - granted())
    assert missing == [], f"the code calls these (or what AWS evaluates them as) and the stack never grants them: {missing}"


def test_the_orchestrator_cannot_rewrite_a_harness_because_nothing_needs_to():
    # Tools travel with each invocation, so nothing updates a harness. Not granting it is the
    # least privilege that works, and it is the permission whose absence broke the first attempt.
    assert not {"UpdateHarness", "UpdateAgentRuntime", "UpdateAgentRuntimeEndpoint"} & granted()
    assert "._control.update_harness(" not in AGENTCORE


def test_the_orchestrator_may_pass_the_dynamic_bot_role_and_only_for_agentcore():
    block = statement_for("orchestratorFn", "PassDynamicAgentExecutionRoleOnly")
    assert "iam:PassRole" in block
    assert "dynamicAgentRole.roleArn" in block
    assert "'iam:PassedToService': 'bedrock-agentcore.amazonaws.com'" in block


def test_the_orchestrator_knows_which_role_a_new_bot_gets():
    assert "orchestratorFn.addEnvironment('AGENT_ROLE_ARN', dynamicAgentRole.roleArn)" in STACK



def test_current_create_harness_dependencies_are_pinned_explicitly():
    # AWS's current Harness authorization table (2026-09-21) evaluates these
    # dependent actions in addition to the public CreateHarness name.
    # CreateWorkloadIdentity and GetAgentRuntime are the two that already
    # produced a live CREATE_FAILED on a new signup; the control-plane call
    # does not spell them, so the same-named-action scan will not catch a drop.
    required = {"CreateHarness", "CreateAgentRuntime", "CreateMemory", "GetMemory",
                "TagResource", "CreateHarnessEndpoint", "CreateAgentRuntimeEndpoint",
                "CreateWorkloadIdentity", "GetAgentRuntime"}
    assert required <= granted()


def test_api_can_invoke_itself_for_harness_warmup_without_a_cfn_cycle():
    block = statement_for("apiFn", "InvokeSelfForHarnessWarmup")
    assert "lambda:InvokeFunction" in block
    assert "function:amazai-api" in block
    # The construct token (`apiFn.functionArn`) cycles the role through the
    # function. The literal name is what the orchestrator already uses.
    assert "apiFn.functionArn" not in block


def test_runtime_permissions_are_split_by_caller():
    m = re.search(
        r"for \(const fn of \[apiFn, orchestratorFn\]\) \{\s*"
        r"fn\.addToRolePolicy\(new iam\.PolicyStatement\(\{\s*"
        r"sid: 'ProvisionAccountHarness'.*?\}\)\);\s*\}",
        STACK, re.S)
    assert m, "API and orchestrator do not share the provisioning-only policy"
    provision_scope = m.group(0)
    actions_match = re.search(r"const harnessProvisionActions = \[(.*?)\];", STACK, re.S)
    assert actions_match
    provision = actions_match.group(1)
    invoke = statement_for("orchestratorFn", "InvokeAccountHarness")
    command = statement_for("apiFn", "CommandAccountHarness")
    assert "apiFn, orchestratorFn" in provision_scope and "routineFn" not in provision_scope
    assert "CreateHarness" in provision and "InvokeHarness" not in provision
    assert "InvokeHarness" in invoke and "InvokeAgentRuntimeCommand" not in invoke
    assert "InvokeAgentRuntimeCommand" in command and "CreateHarness" not in command


def test_routine_worker_has_no_agentcore_control_plane_policy():
    assert not re.search(r"routineFn\.addToRolePolicy\(new iam\.PolicyStatement\(\{.*?bedrock-agentcore:",
                         STACK, re.S)
