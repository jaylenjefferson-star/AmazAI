#!/usr/bin/env bash
# One-command deploy for AmazAI.
#
# Run this on YOUR machine with YOUR AWS credentials. Do not run it anywhere
# you would not store an admin credential.
#
#   ./scripts/deploy.sh            full deploy
#   ./scripts/deploy.sh --check    verify prerequisites and stop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_python.sh
. "$ROOT/scripts/_python.sh"
REGION="${AWS_REGION:-us-west-2}"
CHECK_ONLY="${1:-}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- preflight
step "Checking prerequisites"
fail=0

IN_CLOUDSHELL=0
[ -n "${AWS_EXECUTION_ENV:-}" ] && [[ "${AWS_EXECUTION_ENV}" == *CloudShell* ]] && IN_CLOUDSHELL=1
[ -d /home/cloudshell-user ] && IN_CLOUDSHELL=1
[ "$IN_CLOUDSHELL" = 1 ] && ok "running in AWS CloudShell"

for tool in aws node npm; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool"; else bad "$tool not found"; fail=1; fi
done

if resolve_python; then
  ok "python  $PY_VERSION"
else
  bad "$(python_floor_message)"
  fail=1
fi

# CloudShell gives 1 GB of persistent home. node_modules for the CDK app and
# the console together exceed that, so both are installed under /tmp, which is
# ephemeral but large. Nothing we need to keep lives there.
if [ "$IN_CLOUDSHELL" = 1 ]; then
  AVAIL_MB=$(df -Pm "$HOME" | awk 'NR==2 {print $4}')
  if [ "${AVAIL_MB:-0}" -lt 300 ]; then
    bad "only ${AVAIL_MB}MB free in \$HOME; run 'rm -rf ~/.npm ~/.cache' and retry"
    fail=1
  else
    ok "disk: ${AVAIL_MB}MB free in \$HOME"
  fi
  export npm_config_cache=/tmp/.npm
fi

if ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
  ok "AWS credentials valid (account $ACCOUNT, region $REGION)"
else
  bad "AWS credentials are not usable. Run 'aws configure' or set AWS_PROFILE."
  fail=1
fi

# Model IDs must be real before any harness is created. Guessing one produces
# a failure that presents as a permissions bug.
if "$PY" - "$ROOT/scripts/seats.json" <<'PY'
import json, sys
seats = json.load(open(sys.argv[1]))["seats"]
missing = [s["key"] for s in seats if s.get("enabled") and not s.get("modelId")]
sys.exit(1 if missing else 0)
PY
then
  ok "seats.json has a modelId for every enabled seat"
else
  bad "seats.json has enabled seats with modelId: null (decision D2)"
  echo
  echo "      Resolve them from what this account actually offers:"
  echo "        $PY scripts/resolve_models.py --region $REGION            # preview"
  echo "        $PY scripts/resolve_models.py --region $REGION --write    # apply"
  echo "        $PY scripts/resolve_models.py --region $REGION --write --best"
  echo "            (--best uses the most capable model for every seat)"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo; bold "Fix the above, then re-run."; exit 1
fi

[ "$CHECK_ONLY" = "--check" ] && { echo; bold "All prerequisites met."; exit 0; }

# ------------------------------------------------------------------- build
step "Running tests"
(cd "$ROOT" && "$PY" -m pytest -q)

step "Building the boto3 layer"
"$ROOT/scripts/build_layer.sh"

link_modules_to_tmp() {
  # CloudShell home is 1 GB; node_modules is the only thing that threatens it.
  local dir="$1"
  [ "$IN_CLOUDSHELL" = 1 ] || return 0
  [ -L "$dir/node_modules" ] && return 0
  rm -rf "$dir/node_modules"
  mkdir -p "/tmp/amazai-modules/$(basename "$dir")"
  ln -s "/tmp/amazai-modules/$(basename "$dir")" "$dir/node_modules"
}

step "Building the console"
link_modules_to_tmp "$ROOT/web"
(cd "$ROOT/web" && npm install --silent && npm run build)

# ------------------------------------------------------------------ deploy
step "Deploying infrastructure"
cd "$ROOT/infra"
link_modules_to_tmp "$ROOT/infra"
npm install --silent
npx cdk bootstrap "aws://$ACCOUNT/$REGION" 2>&1 | tail -2
npx cdk deploy --require-approval any-change --outputs-file "$ROOT/.cdk-outputs.json"

# ----------------------------------------------------------------- wire up
step "Reading stack outputs"
eval "$("$PY" - "$ROOT/.cdk-outputs.json" <<'PY'
import json, sys
out = json.load(open(sys.argv[1]))["AmazaiStack"]
for key, var in [("ApiUrl","API_URL"), ("WsUrl","WS_URL"),
                 ("UserPoolId","USER_POOL_ID"),
                 ("UserPoolClientId","USER_POOL_CLIENT_ID"),
                 ("ConsoleUrl","CONSOLE_URL")]:
    val = out.get(key + "Output") or out.get(key, "")
    print(f'{var}="{val}"')
PY
)"
ok "API      $API_URL"
ok "Console  $CONSOLE_URL"

step "Writing web/.env"
cat > "$ROOT/web/.env" <<ENV
VITE_API_URL=$API_URL
VITE_WS_URL=$WS_URL
VITE_USER_POOL_ID=$USER_POOL_ID
VITE_USER_POOL_CLIENT_ID=$USER_POOL_CLIENT_ID
ENV
ok "web/.env written from the stack outputs"

step "Rebuilding the console against the real endpoints and redeploying"
(cd "$ROOT/web" && npm run build)
npx cdk deploy --require-approval never --outputs-file "$ROOT/.cdk-outputs.json" >/dev/null
ok "console deployed"

# ---------------------------------------------------------------- next steps
OWNER_EMAIL="${AMAZAI_OWNER_EMAIL:-jaylen.jefferson@amazflow.com}"
cat <<NEXT

$(bold "Deployed.")

Two things left, both needing your input:

  1. Create your account and set a password:

       aws cognito-idp admin-create-user \\
         --user-pool-id $USER_POOL_ID \\
         --username $OWNER_EMAIL --region $REGION

       aws cognito-idp admin-set-user-password \\
         --user-pool-id $USER_POOL_ID \\
         --username $OWNER_EMAIL --password '<a strong password>' \\
         --permanent --region $REGION

     Then find your Cognito sub -- the provisioner needs it as OWNER_ID:

       aws cognito-idp admin-get-user --user-pool-id $USER_POOL_ID \\
         --username $OWNER_EMAIL --region $REGION \\
         --query 'UserAttributes[?Name==\`sub\`].Value' --output text

  2. Create the agent seats:

       OWNER_ID=<the sub from above> $PY scripts/provision_agents.py

Then open: $CONSOLE_URL
You will be asked to enrol an authenticator app on first sign-in.

NEXT
