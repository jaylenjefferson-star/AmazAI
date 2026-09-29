# Deploying from AWS CloudShell

CloudShell is a terminal inside the AWS console, already authenticated by the
browser session you are signed into. It needs no access keys, no local install,
and nothing pasted anywhere — which makes it the shortest safe path from
"signed into AWS in Chrome" to a running AmazAI.

## Before you start

**Enable Bedrock model access.** A fresh account has every model switched off,
and the resulting failure looks like a permissions bug rather than a missing
opt-in. Do this first:

> Bedrock console → **Model access** → enable the Anthropic Claude models →
> `https://us-west-2.console.aws.amazon.com/bedrock/home?region=us-west-2#/modelaccess`

Approval is usually instant.

## If you want me to drive

Run this one command and paste the whole output back to me. It is read-only —
it creates nothing, changes nothing, and costs nothing — and it tells me your
account's model access, AgentCore permissions, CDK state and tooling versions
in one go, so I can fix whatever is wrong precisely instead of guessing.

```bash
git clone https://github.com/jaylenjefferson-star/AmazAI.git && cd AmazAI
./scripts/doctor.sh
```

Your account ID is masked by default.

## Five commands

Open CloudShell (the `>_` icon in the console toolbar, top right). Pick the
**us-west-2** region in the console before opening it — CloudShell inherits it,
and AgentCore session storage is not available everywhere.

```bash
# 1. Get the code
git clone https://github.com/jaylenjefferson-star/AmazAI.git
cd AmazAI

# 2. See what your account can actually run
python3 scripts/resolve_models.py

# 3. Lock those model IDs in (--best = most capable model for every seat)
python3 scripts/resolve_models.py --write --best

# 4. Confirm everything is ready. Read-only; changes nothing.
./scripts/deploy.sh --check

# 5. Deploy
./scripts/deploy.sh
```

Step 5 takes roughly 10–15 minutes, mostly CloudFront. It finishes by printing
the two remaining commands — creating your login and provisioning the seats —
with your real pool ID already filled in.

## Notes specific to CloudShell

- **Disk.** Home is 1 GB and persists; `/tmp` is large and does not. The deploy
  script symlinks `node_modules` into `/tmp` automatically, so the limit is not
  the binding constraint. Nothing that matters lives there.
- **Timeouts.** CloudShell closes an idle session after ~20 minutes. The deploy
  prints continuously, so it will not idle out mid-run — but do not walk away
  between steps 4 and 5.
- **Re-running.** `./scripts/deploy.sh` is safe to run again. CDK deploys are
  idempotent, and `provision_agents.py` reuses an existing READY harness rather
  than creating a second one.
- **If the session drops** after `cdk deploy` started, the stack still finishes
  in CloudFormation. Re-run the script; it will pick up from the current state.

## What it costs to leave running

Idle cost is close to zero — nothing runs between conversations. The only line
that can surprise you is model token volume, which is why every seat has a
`maxTokens` ceiling and a per-run and per-month budget enforced in code rather
than described in a prompt.

## If something fails

The failure modes worth knowing, in the order you are likely to hit them:

| Symptom | Cause |
|---|---|
| `resolve_models.py` finds nothing | Model access not enabled — see above |
| `AccessDeniedException` on `bedrock-agentcore` | Your IAM identity lacks AgentCore permissions |
| `cdk bootstrap` fails | The account has never used CDK in this region; the script runs it for you, but it needs permission to create the bootstrap stack |
| Console loads but sign-in fails | Auth0 callback URL, client id, or audience does not match this deploy — see the printed next steps and `config/auth0.json` |
| Agent replies with "no modelId configured" | `seats.json` was not written; re-run step 3 |

Send me whatever it prints and I will fix it.
