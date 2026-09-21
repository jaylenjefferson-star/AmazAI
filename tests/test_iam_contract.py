"""What the code calls in AWS is what the stack grants.

The tool sync shipped calling `update_harness` from the orchestrator, whose role was
never granted `bedrock-agentcore:UpdateHarness`. The call was denied, logged and
swallowed (by design: a failed sync must not stop a run), so nothing failed loudly and
no test could see it -- the gap was between two files in two languages.

This reads the control-plane calls `agentcore.py` makes and checks the CDK stack grants
each one, and that the orchestrator holds the other permissions creating a Bot needs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACK = (ROOT / "infra" / "lib" / "amazai-stack.ts").read_text()
AGENTCORE = (ROOT / "services" / "amazai" / "agentcore.py").read_text()


def pascal(snake: str) -> str:
    return "".join(part.title() for part in snake.split("_"))


def statement_for(function: str, sid: str) -> str:
    """The source of `<function>.addToRolePolicy(... sid: '<sid>' ...)`."""
    m = re.search(rf"{function}\.addToRolePolicy\(new iam\.PolicyStatement\(\{{\s*sid: '{sid}'.*?\}}\)\);",
                  STACK, re.S)
    assert m, f"{function} has no policy statement {sid!r}"
    return m.group(0)


def test_every_control_plane_call_the_code_makes_is_granted_to_the_orchestrator():
    calls = set(re.findall(r"self\._control\.(\w+)\(", AGENTCORE))
    assert {"create_harness", "get_harness", "update_harness"} <= calls, calls
    granted = set(re.findall(r"'bedrock-agentcore:(\w+)'", STACK))
    missing = sorted(pascal(c) for c in calls if pascal(c) not in granted)
    assert missing == [], f"called but never granted anywhere in the stack: {missing}"


def test_updating_a_harness_is_granted_to_the_orchestrator_specifically():
    assert "bedrock-agentcore:UpdateHarness" in statement_for("orchestratorFn", "KeepHarnessToolsCurrent")


def test_the_orchestrator_may_pass_the_dynamic_bot_role_and_only_for_agentcore():
    block = statement_for("orchestratorFn", "PassDynamicAgentExecutionRoleOnly")
    assert "iam:PassRole" in block
    assert "dynamicAgentRole.roleArn" in block
    assert "'iam:PassedToService': 'bedrock-agentcore.amazonaws.com'" in block


def test_the_orchestrator_knows_which_role_a_new_bot_gets():
    assert "orchestratorFn.addEnvironment('AGENT_ROLE_ARN', dynamicAgentRole.roleArn)" in STACK
