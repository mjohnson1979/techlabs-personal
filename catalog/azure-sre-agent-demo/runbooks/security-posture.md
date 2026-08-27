# Security posture runbook

## Scope

Review only the Container App ingress configuration in the connected demo resource group.

## Investigation

Inspect whether external ingress permits insecure HTTP and confirm the HTTPS endpoint is otherwise healthy.

## Allowed action

If `allowInsecure` is `true`, update only that property to `false`.

## Verification

Confirm `allowInsecure` is false, HTTPS returns HTTP 200, and plaintext HTTP is rejected or redirected to HTTPS.

## Stop conditions

Stop without changes if the app is already HTTPS-only, the target is outside the demo resource group, or remediation requires deletion, RBAC changes, Key Vault access, identity changes, or subscription-wide writes.