#!/usr/bin/env bash
# AmazAI environment diagnostic.
#
# Read-only. Creates nothing, changes nothing, costs nothing.
# Run it in AWS CloudShell and paste the whole output back.
#
#   ./scripts/doctor.sh                 # account ID masked
#   ./scripts/doctor.sh --show-account  # include it (rarely needed)
set -uo pipefail

SHOW_ACCOUNT=0
[ "${1:-}" = "--show-account" ] && SHOW_ACCOUNT=1

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_python.sh
. "$ROOT/scripts/_python.sh"

pass() { printf '  [ok]   %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

echo "AmazAI doctor — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "region: $REGION"

# ------------------------------------------------------------------ tooling
section "Tooling"
for t in aws node npm git; do
  if command -v "$t" >/dev/null 2>&1; then
    case "$t" in
      aws)  v=$(aws --version 2>&1 | head -1) ;;
      node) v=$(node --version 2>&1) ;;
      npm)  v=$(npm --version 2>&1) ;;
      git)  v=$(git --version 2>&1) ;;
    esac
    pass "$t  $v"
  else
    fail "$t not installed"
  fi
done

# Reported separately from the others because "installed" is not the question
# for Python here — "new enough" is, and the default one on macOS is not.
if resolve_python; then
  pass "python  $PY_VERSION  ($PY)"
else
  fail "$(python_floor_message)"
fi

if [ -d /home/cloudshell-user ] || [[ "${AWS_EXECUTION_ENV:-}" == *CloudShell* ]]; then
  pass "running in AWS CloudShell"
  printf '  [info] home free: %sMB   /tmp free: %sMB\n' \
    "$(df -Pm "$HOME" | awk 'NR==2{print $4}')" \
    "$(df -Pm /tmp   | awk 'NR==2{print $4}')"
fi

# ----------------------------------------------------------------- identity
section "Identity"
IDENT=$(aws sts get-caller-identity --output json 2>&1)
if echo "$IDENT" | grep -q '"Account"'; then
  ACCOUNT=$(echo "$IDENT" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Account"])')
  ARN=$(echo "$IDENT" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')
  if [ "$SHOW_ACCOUNT" = 1 ]; then
    pass "account $ACCOUNT"
  else
    pass "account ****${ACCOUNT: -4}  (masked; --show-account to reveal)"
  fi
  # Strip the account number out of the ARN too.
  pass "identity $(echo "$ARN" | sed "s/$ACCOUNT/****/")"
else
  fail "no usable credentials"
  echo "$IDENT" | head -3 | sed 's/^/         /'
  echo
  echo "  Nothing below will work until this line passes."
  exit 1
fi

# ------------------------------------------------------------------ bedrock
section "Bedrock model access"
MODELS=$(aws bedrock list-foundation-models --by-provider anthropic \
          --region "$REGION" --output json 2>&1)
if echo "$MODELS" | grep -q '"modelSummaries"'; then
  COUNT=$(echo "$MODELS" | "$PY" -c '
import json,sys
m=[x for x in json.load(sys.stdin)["modelSummaries"] if "claude" in x["modelId"].lower()]
print(len(m))
for x in sorted(m, key=lambda y: y["modelId"])[:40]:
    on = "ON_DEMAND" in (x.get("inferenceTypesSupported") or [])
    label = "on-demand " if on else "profile-only"
    print(f"    {label} {x[\"modelId\"]}")
')
  N=$(echo "$COUNT" | head -1)
  if [ "${N:-0}" -gt 0 ]; then
    pass "$N Claude foundation models listed"
    echo "$COUNT" | tail -n +2
  else
    fail "no Claude models listed"
  fi
else
  fail "list-foundation-models failed"
  echo "$MODELS" | head -3 | sed 's/^/         /'
fi

PROFILES=$(aws bedrock list-inference-profiles --region "$REGION" --output json 2>&1)
if echo "$PROFILES" | grep -q 'inferenceProfileSummaries'; then
  echo "$PROFILES" | "$PY" -c '
import json,sys
p=[x for x in json.load(sys.stdin)["inferenceProfileSummaries"]
   if "anthropic" in x.get("inferenceProfileId","").lower()]
print(f"  [ok]   {len(p)} Anthropic inference profiles")
for x in sorted(p, key=lambda y: y["inferenceProfileId"])[:40]:
    status = x.get("status", "?")
    print(f"    {status:8s} {x[\"inferenceProfileId\"]}")
' || warn "could not parse inference profiles"
else
  warn "list-inference-profiles unavailable here"
  echo "$PROFILES" | head -2 | sed 's/^/         /'
fi

echo
echo "  If both lists are empty, model access is switched off for this account:"
echo "    https://$REGION.console.aws.amazon.com/bedrock/home?region=$REGION#/modelaccess"

# ---------------------------------------------------------------- agentcore
section "Bedrock AgentCore"
AC=$(aws bedrock-agentcore-control list-harnesses --region "$REGION" --output json 2>&1)
if echo "$AC" | grep -qE 'harness|Summaries|\[\]'; then
  pass "bedrock-agentcore-control reachable; list-harnesses succeeded"
  echo "$AC" | head -20 | sed 's/^/         /'
elif echo "$AC" | grep -qi 'AccessDenied\|not authorized'; then
  fail "AccessDenied on bedrock-agentcore-control — your identity lacks AgentCore permissions"
  echo "$AC" | head -2 | sed 's/^/         /'
elif echo "$AC" | grep -qi 'Invalid choice\|argument operation'; then
  fail "this AWS CLI does not know bedrock-agentcore-control — it is too old"
  echo "         Update it, or run the deploy from a machine with a current CLI."
else
  warn "unexpected response:"
  echo "$AC" | head -4 | sed 's/^/         /'
fi

# --------------------------------------------------------------------- cdk
section "CDK bootstrap"
BOOT=$(aws cloudformation describe-stacks --stack-name CDKToolkit \
        --region "$REGION" --query 'Stacks[0].StackStatus' --output text 2>&1)
case "$BOOT" in
  CREATE_COMPLETE|UPDATE_COMPLETE) pass "CDKToolkit present ($BOOT)" ;;
  *does\ not\ exist*)              warn "not bootstrapped; deploy.sh will run cdk bootstrap" ;;
  *)                               warn "CDKToolkit status: $BOOT" ;;
esac

STACK=$(aws cloudformation describe-stacks --stack-name AmazaiStack \
        --region "$REGION" --query 'Stacks[0].StackStatus' --output text 2>&1)
case "$STACK" in
  *does\ not\ exist*) pass "AmazaiStack not deployed yet (expected on a first run)" ;;
  CREATE_COMPLETE|UPDATE_COMPLETE) pass "AmazaiStack already deployed ($STACK)" ;;
  *) warn "AmazaiStack status: $STACK" ;;
esac

# ------------------------------------------------------------------- repo
section "Repository"
if [ -f "$ROOT/scripts/seats.json" ]; then
  "$PY" - "$ROOT/scripts/seats.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
for s in cfg["seats"]:
    state = "enabled " if s.get("enabled") else "disabled"
    model = s.get("modelId") or "NOT SET  <- run resolve_models.py --write --best"
    print(f"  [info] {state} {s['key']:9s} {model}")
PY
else
  fail "scripts/seats.json missing — are you in the repo root?"
fi

section "Summary"
echo "  Paste everything above back to Claude."
echo "  Nothing here was created, changed, or charged."
