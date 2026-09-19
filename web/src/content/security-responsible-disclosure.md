---
title: Security & Responsible Disclosure
version: 2026.09
effective: 2026-09-19
last_updated: 2026-09-19
contact: security@amazai.co
---

# Security & Responsible Disclosure

**In short:** AmazAI agents may act on a user’s behalf, so our security model is designed around boundaries: isolated workspaces, scoped connectors, human approval gates, and a record of material actions. We are transparent about the controls we use and the compliance claims we do not make.

## 1. Our security approach

AmazFlow, LLC, a Georgia limited liability company, doing business as AmazAI ("AmazAI," "we," "us," or "our"), designs the Service to limit authority, separate workspaces, and preserve accountability. Our safeguards are administrative, technical, and organizational measures that are intended to reduce risk; they are not a guarantee that no incident will occur.

The Service is designed around the following principles:

- **Least privilege.** Users, agents, routines, and connectors should have only the access needed for their authorized purpose.
- **Isolated agent workspaces.** Each agent operates in a sandboxed workspace intended to be separated from other agents and users.
- **Scoped connectors.** Agents use only the credentials and permissions that an authorized user grants. Connector credentials are encrypted at rest and are not displayed to agents as plain text.
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

We welcome good-faith reports of potential security vulnerabilities. Please email **security@amazai.co** with a clear description of the issue, the affected component or URL, and steps that allow us to reproduce the issue safely. Screenshots, timestamps, and a proposed remediation are helpful when available.

When conducting security research, please:

- act in good faith and use the least intrusive method reasonably available;
- do not access, alter, download, or delete data that is not yours;
- do not disrupt, degrade, or deny service to other users;
- do not use social engineering, phishing, physical attacks, or automated scanning that could impair the Service;
- do not publicly disclose the issue until we have had a reasonable opportunity to investigate and remediate it; and
- comply with applicable law and avoid actions that could create risk for customers or other users.

We will review reports in good faith, acknowledge receipt when practical, and provide status updates as appropriate. We will not pursue action against a researcher for good-faith, authorized research that follows these guidelines. This statement does not authorize testing of third-party systems, customer systems, or any activity prohibited by law.

## 5. Security incidents

If we confirm an incident affecting personal information or Workspace Content, we will investigate, take reasonable steps to contain and remediate it, and provide notice to affected customers or individuals when required by applicable law or contract. The timing and content of notice may depend on the facts of the incident, legal obligations, and law-enforcement considerations.

## 6. Contact

For security questions, suspected misuse, or vulnerability reports, contact **security@amazai.co**. Privacy requests should be sent to **privacy@amazai.co**.
