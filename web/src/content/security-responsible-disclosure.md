---
title: Security & Responsible Disclosure
version: 2026.09
effective: 2026-09-28
last_updated: 2026-09-28
contact: security@amazai.co
---

# Security & Responsible Disclosure

**In short:** AmazAI agents may act on a user’s behalf, so our security model is designed around boundaries: isolated workspaces, scoped connectors, human approval gates, and a record of material actions. We are transparent about the controls we use and the compliance claims we do not make.

## 1. Our security approach

AmazFlow, LLC, a Georgia limited liability company, doing business as AmazAI ("AmazAI," "we," "us," or "our"), designs the Service to limit authority, separate workspaces, and preserve accountability. Our safeguards are administrative, technical, and organizational measures that are intended to reduce risk; they are not a guarantee that no incident will occur.

The Service is designed around the following principles:

- **Least privilege.** Users, agents, routines, and connectors should have only the access needed for their authorized purpose.
- **Isolated companion sessions and workspaces.** Companion execution is scoped to the authenticated account, companion, and conversation. Working storage is intended to be separated from other accounts and sessions, and durable files are stored under account- and companion-scoped paths.
- **Scoped connectors.** Companions use only connectors and permissions granted for the applicable account and workflow. For Composio-managed connections, Composio stores the underlying provider credential and AmazAI uses a connected-account reference to request authorized execution. Underlying provider credentials are not placed in model prompts.
- **Human approval gates.** High-impact actions and scheduled routines can require approval before execution. Customers determine the approval thresholds appropriate for their workflows.
- **Activity records.** Material agent actions, handoffs, and approvals are recorded so users can review who acted, what occurred, and when.
- **Encryption.** Information is encrypted in transit and at rest using measures designed for the applicable component and risk.
- **Secure development.** We use access controls, code review, testing, dependency and vulnerability management, and other practices designed to improve the security of the Service over time.

## 2. Customer responsibilities

Security is a shared responsibility. Customers and users are responsible for protecting their accounts, choosing appropriate connector permissions, reviewing agents and routines before enabling them, maintaining suitable approval gates, and promptly revoking access that is no longer authorized.

You should not use the Service with protected health information, payment-card data, or other regulated information unless the applicable deployment, connector, and written agreement have been expressly approved for that use. Do not share credentials or sensitive records in unapproved support channels.

## 3. Compliance posture

AmazAI does not currently claim SOC 2 attestation or blanket HIPAA compliance. A reference to a safeguard, policy, or security practice is descriptive of our current program and does not create a contractual compliance commitment or certification.

If a customer needs a particular regulatory, security, or data-processing commitment, the parties must address that requirement in a written agreement before the applicable data or workload is introduced to the Service.

## 4. Reporting a vulnerability

We welcome good-faith reports of potential security vulnerabilities affecting AmazAI-operated systems, including **amazai.co**, **api.amazai.co**, and the AmazAI application. Please email **security@amazai.co** with a clear description, the affected component or URL, the least-sensitive proof needed to demonstrate impact, and safe reproduction steps. Screenshots and timestamps are helpful when they do not expose another person’s data. Do not send credentials, access tokens, or unnecessary personal information.

When conducting security research, please:

- act in good faith and use the least intrusive method reasonably available;
- do not access, alter, download, or delete data that is not yours;
- do not disrupt, degrade, or deny service to other users;
- do not use social engineering, phishing, physical attacks, or automated scanning that could impair the Service;
- do not test a third-party connected service, model provider, identity provider, payment processor, customer system, or other vendor unless that provider separately authorizes the testing;
- stop and report the issue if testing unexpectedly exposes another person’s data or creates a risk of harm;
- do not publicly disclose the issue until we have had a reasonable opportunity to investigate and remediate it; and
- comply with applicable law and avoid actions that could create risk for customers or other users.

We aim to acknowledge a complete report within five business days and will provide status updates when practical. Response and remediation time depends on severity, reproducibility, affected systems, and coordination with providers. AmazAI does not currently offer a public bug bounty or promise payment for a report.

We will not pursue legal action against a researcher for good-faith, authorized research that follows these guidelines, promptly reports the issue, avoids privacy violations and service disruption, and gives us a reasonable opportunity to remediate before public disclosure. This statement does not authorize testing of third-party systems, customer systems, or activity prohibited by law.

## 5. Security incidents

We maintain a process to investigate suspected unauthorized access to or disclosure, alteration, loss, or destruction of personal information or Workspace Content. If we confirm such an incident, we will take reasonable steps to contain, investigate, and remediate it and will notify affected customers or individuals without undue delay when required by applicable law or contract.

As information becomes reasonably available, a notice may describe the nature and known scope, categories of affected information and users, known or reasonably likely consequences, containment or remediation measures, and a contact for follow-up. Information may be provided in phases. We may delay or limit notice where required by law enforcement, prohibited by law, or necessary to avoid increasing the risk of harm. Customer-specific notification periods must be stated in a signed agreement; this public policy does not promise a certification, service level, or fixed incident-notice deadline.

## 6. Contact

For security questions, suspected misuse, or vulnerability reports, contact **security@amazai.co**. Privacy requests should be sent to **privacy@amazai.co**.
