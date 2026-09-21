# 13 — AWS service decisions

**Deliverable 7.**

## Use now (V1)

| Service | Role | Why now |
|---|---|---|
| **Bedrock AgentCore Harness / Runtime** | The agent's computer | The primitive the whole product rests on. Not something to rebuild. |
| **AgentCore Identity** | Token vault, OAuth flows | Keeps tokens out of the runtime entirely. Free when used via Runtime or Gateway. |
| **AgentCore Gateway** | Connector tools, egress credential injection | $0.005 per 1,000 invocations. Negligible. |
| **Cognito** | The one login | Free at this scale. MFA built in. |
| **DynamoDB** | Single table, all control-plane state | On-demand, near-zero idle, PITR. Access patterns are all owner-scoped. |
| **Lambda** (Python 3.12, arm64) | `api`, `ws`, `orchestrator`, `routine`, `sweeper` | Pay-per-use. Idle is genuinely $0. |
| **API Gateway HTTP API** | REST + JWT authorizer | Cheaper than REST API; the Cognito authorizer is built in. |
| **API Gateway WebSocket** | Streaming + notifications | Lambda response streaming is Node-only; the verified AgentCore surface is boto3. |
| **EventBridge Scheduler** | Routine triggers | Timezone-aware, handles DST, one schedule per routine. |
| **EventBridge (bus + rules)** | Sweeper tick, connector events | — |
| **S3** | Drive bucket, evidence bucket, console hosting | Versioning is the snapshot strategy. |
| **CloudFront** | Console delivery | OAC, 403/404 → `index.html`. |
| **KMS** | CMK for buckets, browser profiles, vault | One key, explicit grants. |
| **SES** | Digests, approval nudges | Cheap. |
| **CloudWatch Logs** | Per-agent log groups, 90-day retention | Set retention explicitly or this becomes the surprise line item. |
| **STS** | Temporary AWS credentials, ≤15 min, session-tagged | The alternative is a standing credential. There isn't one. |
| **IAM** | Per-agent execution roles, investigate/change split | Revision #1. The boundary that makes seats real. |
| **EC2 Desktop** (optional escape hatch) | A narrow, opt-in full computer for a run that genuinely needs one | The default is AgentCore. EC2 Desktop exists only for persistent developer environments, OS-level applications, or heavy local tooling the ephemeral microVM cannot host. It is not a browser fleet and not the default. See [20](20-hybrid-compute.md). |

## Later

| Service | For | Trigger to adopt |
|---|---|---|
| **AWS IoT Core** | Local companion transport | M5. Right service: outbound-dialing devices, per-device certs, topic-scoped policies. |
| **Pinpoint / SNS mobile push** | Push notifications | M4. |
| **AgentCore Memory** | Managed agent memory | Evaluate at M3. Own DynamoDB rows for now because memory must be *user-editable and inspectable* ([15](15-open-decisions.md) D3). |
| **VPC + NAT + EFS** | Shared live filesystem across agents | Only if several agents must write one filesystem *simultaneously*. S3 covers everything short of that, and this costs ~$32/mo before a byte moves. |
| **Step Functions** | Orchestration | If the state machine outgrows DynamoDB + Lambda. It currently doesn't: pauses can last 14 days and resume is a re-invoke, not a callback. |
| **Fargate** | Long-running orchestrator | If the 15-minute ceiling stops being an edge case. The **Continue** action handles it until then. |
| **Athena + S3 evidence** | Audit queries across runs | When "what did my agents do last quarter" becomes a real question. |
| **Cost Explorer / CUR** | True AWS-side cost attribution | When the run-level ledger needs reconciling against the bill. |
| **Secrets Manager** | Non-OAuth API keys | When a connector arrives that the token vault can't hold. |
| **WAF** | Webhook endpoint protection | When public webhooks go live (M2). |
| **Backup / cross-region replication** | DR | When losing the audit history would matter more than it does today. |

## Avoid for now

| Service | Why | Cost avoided |
|---|---|---|
| **NAT Gateway** | Forced by VPC mode; needed only for EFS. S3 via the sandbox's own network avoids the whole question. | **~$32/mo** |
| **Always-on Fargate + ALB** | An idle control plane is the opposite of this architecture's posture. | **~$26/mo** |
| **AgentCore Web Search** | $7 per 1,000 queries. Point a Gateway target at a cheaper search API. | Potentially large |
| **Aurora / RDS** | DynamoDB fits every access pattern here; a relational engine is idle cost plus ops. | ~$15–45/mo |
| **OpenSearch** | Evidence search over S3 is an Athena problem, later. | ~$50+/mo |
| **Bedrock Knowledge Bases** | No RAG requirement. Memory is explicit rows, not a vector index. | — |
| **Amazon Q** | Overlaps the product. | — |
| **EC2 browser fleet** | The microVM already has Chromium. A browser fleet is idle cost plus standing sessions. Distinct from the EC2 Desktop escape hatch above, which is not a browser fleet: it never stands up per-agent browsers, it is opt-in per run, and it exists for full-computer workloads the microVM cannot host. | ~$25–60/mo per agent |
| **WorkSpaces / DCV streaming** | Full interactive desktop *streaming* is large work for ~1% of run duration. Screenshot + takeover covers it. This remains avoided for its own reasons and is not what the EC2 Desktop escape hatch is: that escape hatch runs full-computer *work* (persistent dev env, OS-level apps, heavy tooling), it does not stream a desktop to a human. | ~$25+/mo |
| **ECS/EKS** | Nothing needs a scheduler. | — |
| **Multi-region anything** | Single user, single region. | — |

## The shape this produces

Idle cost is near zero. Nothing runs between conversations. The only line that
can surprise you is model token volume — which is why `maxTokens` is pinned per
seat, per-run budgets hard-stop, and the cost ledger is written from day one.

**Region: us-west-2** (or us-east-1 / us-east-2). Session storage is not
available in every region — confirm before deploying elsewhere.

> Before deploying, enable model access for the Claude models you intend to use
> in the Bedrock console. A fresh account has them switched off and the failure
> presents as a permissions bug.
