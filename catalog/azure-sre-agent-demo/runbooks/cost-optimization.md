# Cost optimization runbook

## Scope

Analyze and right-size only the Container App in the connected demo resource group.

## Investigation

Review at least 30 minutes of replica count, CPU, memory, request volume, and availability data. Query resource-group cost data when available, but do not block on Cost Management ingestion latency.

## Allowed action

When average CPU is below 10 percent, memory is below 30 percent, request volume is low, and availability is healthy, reduce `minReplicas` from 3 to 1. Do not change CPU, memory, maximum replicas, image, ingress, identity, or any other resource.

## Verification

Confirm `minReplicas` is 1, the HTTPS root endpoint returns HTTP 200, and no new availability failure appears.

## Stop conditions

Stop without changes when evidence is insufficient, utilization exceeds a threshold, availability is degraded, the current minimum is not 3, or any action would affect resources outside the demo resource group.