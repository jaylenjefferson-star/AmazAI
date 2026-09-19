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
REGION="${AWS_REGION:-us-west-2}"
CHECK_ONLY="${1:-}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- preflight
step "Checking prerequisites"
fail=0

for tool in aws node npm python3; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool"; else bad "$tool not found"; fail=1; fi
done

if ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
  ok "AWS credentials valid (account $ACCOUNT, region $REGION)"
else
  bad "AWS credentials are not usable. Run 'aws configure' or set AWS_PROFILE."
  fail=1
fi

# Model IDs must be real before any harness is created. Guessing one produces
# a failure that presents as a permissions bug.
if python3 - "$ROOT/scripts/seats.json" <<'PY'
import json, sys
seats = json.load(open(sys.argv[1]))["seats"]
missing = [s["key"] for s in seats if s.get("enabled") and not s.get("modelId")]
sys.exit(1 if missing else 0)
PY
then
  ok "seats.json has a modelId for every enabled seat"
else
  bad "seats.json has enabled seats with modelId: null (decision D2)"
  echo "      Resolve them for this account:"
  echo "        aws bedrock list-inference-profiles --region $REGION"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo; bold "Fix the above, then re-run."; exit 1
fi

[ "$CHECK_ONLY" = "--check" ] && { echo; bold "All prerequisites met."; exit 0; }

# ------------------------------------------------------------------- build
step "Running tests"
(cd "$ROOT" && python3 -m pytest -q)

step "Building the boto3 layer"
"$ROOT/scripts/build_layer.sh"

step "Building the console"
(cd "$ROOT/web" && npm install --silent && npm run build)

# ------------------------------------------------------------------ deploy
step "Deploying infrastructure"
cd "$ROOT/infra"
npm install --silent
npx cdk bootstrap "aws://$ACCOUNT/$REGION" 2>&1 | tail -2
npx cdk deploy --require-approval any-change --outputs-file "$ROOT/.cdk-outputs.json"

# ----------------------------------------------------------------- wire up
step "Reading stack outputs"
eval "$(python3 - "$ROOT/.cdk-outputs.json" <<'PY'
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

       OWNER_ID=<the sub from above> python3 scripts/provision_agents.py

Then open: $CONSOLE_URL
You will be asked to enrol an authenticator app on first sign-in.

NEXT
