# Auto-remediation runbook

## Scope

Investigate the Azure Monitor alert named `SRE demo availability failure` and act only on the Container App in the connected demo resource group.

## Investigation

1. Confirm the alert and failed Application Insights availability locations.
2. Inspect Container App provisioning state, ingress configuration, revisions, replicas, and recent activity logs.
3. Confirm ingress is disabled before changing configuration.

## Allowed action

Enable external ingress on target port 80 with automatic transport and `allowInsecure` set to `false`.

## Verification

1. Confirm the Container App reports a fully qualified domain name.
2. Request its HTTPS root endpoint and require HTTP 200.
3. Confirm the availability alert begins resolving.

## Stop conditions

Stop and report evidence without changing anything if the target is outside the demo resource group, ingress is already healthy, the fault is unrelated to ingress, or the requested action requires deletion, RBAC changes, Key Vault access, or subscription-wide writes.