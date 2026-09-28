---
title: Subprocessor List
version: 2026.09
effective: 2026-09-28
last_updated: 2026-09-28
contact: privacy@amazai.co
---

# Subprocessor List

**In short:** AmazAI relies on a small set of service providers to host the Service, authenticate users, process payments, run model inference, and connect third-party tools. The providers used for a customer may depend on the features and connectors the customer enables.

## Current subprocessors

| Provider | Purpose | Information potentially processed |
| --- | --- | --- |
| Amazon Web Services, Inc. | Cloud hosting, application infrastructure, storage, logs, agent workspaces, and model inference through Amazon Bedrock | Account and service information, Workspace Content, prompts, outputs, files, logs, and technical information as needed for the enabled feature |
| Auth0, Inc. (an Okta company) | Authentication and session management | Name, email address, organization/account identifiers, authentication events, session and device information |
| Stripe, Inc. | Checkout, subscription management, invoices, payment processing, and billing portal | Name, email address, billing identifiers, transaction and subscription information, and payment information provided directly to Stripe |
| Composio, Inc. | Connected-account authorization and execution of actions in third-party services | Connected-account identifiers and credentials, tool arguments, tool results, files, and execution metadata for connectors a customer enables |

Third-party services that a customer connects—such as Google, Microsoft, Slack, GitHub, or Notion—are recipients selected by the customer, not general AmazAI subprocessors merely because a connector is available. Their own terms and privacy policies apply to information processed in those services.

## Model providers

AmazAI currently provides model inference through Amazon Bedrock. The underlying model selected for a task may vary by feature, customer configuration, availability, and service tier. AmazAI does not authorize AWS or an underlying model publisher to use Customer Content to train a general-purpose model. Processing and retention remain subject to the applicable service configuration and customer agreement; this is not a promise of zero processing or zero retention.

## Changes to this list

We may add or replace a subprocessor as the Service changes. We will update this page before or promptly after a material change and revise the last-updated date. Customers with a signed agreement that includes advance subprocessor notice will receive notice through the method and within the period stated in that agreement.

Questions or concerns about a subprocessor may be sent to **privacy@amazai.co**.
