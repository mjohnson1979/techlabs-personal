# Azure VMware Solution Enterprise

This catalog entry provides a starter structure for an enterprise Azure VMware Solution deployment.

## Included Structure

- main.bicep: Top-level orchestration template
- main.bicepparam: Default parameter set
- modules/avs: AVS private cloud baseline
- modules/networking: Virtual network and subnet foundation
- modules/security: Key vault and network security baseline
- modules/connectivity: ExpressRoute gateway and optional circuit connection
- modules/monitoring: Log Analytics, App Insights, and diagnostics
- images/architecture.png: Architecture diagram placeholder
