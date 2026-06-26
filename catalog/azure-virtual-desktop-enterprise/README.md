# Azure Virtual Desktop Enterprise

This catalog entry provides a starter structure for an enterprise Azure Virtual Desktop (AVD) deployment.

## Included Structure

- `main.bicep`: Top-level orchestration template
- `main.bicepparam`: Default parameter set
- `modules/avd`: AVD host pool, app groups, workspace
- `modules/networking`: VNets, subnets, peering, private access patterns
- `modules/security`: RBAC, identity, baseline hardening controls
- `modules/fslogix`: FSLogix profile storage components
- `modules/monitoring`: Logging, diagnostics, and alerts
- `images/architecture.png`: Architecture diagram placeholder
