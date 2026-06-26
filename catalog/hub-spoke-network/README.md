# Hub-and-Spoke Network (Firewall + Bastion)

Enterprise hub-and-spoke topology: a hub VNet with a zone-redundant **Azure Firewall**,
**Azure Bastion**, and a gateway subnet, peered to a workload **spoke** whose default
route is forced through the firewall for egress inspection.

## What this deploys

- **Hub VNet** with `AzureFirewallSubnet`, `AzureBastionSubnet`, and `GatewaySubnet`.
- **Azure Firewall** (Standard/Premium) across zones 1-3 with a reusable **firewall policy** and an egress rule collection.
- **Azure Bastion** (optional) for RDP/SSH to private workloads without public IPs.
- **Spoke VNet** with `workload` and `privateEndpoints` subnets, an NSG denying inbound internet, and a **route table** sending `0.0.0.0/0` to the firewall's private IP.
- **Bidirectional VNet peering** between hub and spoke.
- **Log Analytics** workspace receiving firewall diagnostics.

## Why this exists

Centralized egress inspection, no public IPs on workloads, and a single audited path
to the internet are baseline requirements for regulated landing zones. This is the
connectivity substrate that web, container, data, and AI workloads peer into.

## Potential use cases

- Connectivity subscription for an Azure landing zone.
- Forced-tunnel egress inspection for regulated workloads (PCI, HIPAA, IRAP).
- Secure jump-box access to private VMs via Bastion.
- Central firewall policy shared across multiple spokes.

## Architecture

### Dev

```mermaid
flowchart LR
  inet["Internet"]:::edge
  subgraph hub["Hub VNet"]
    fw["Azure Firewall<br/>Standard, zones 1-3"]:::edge
    gw["GatewaySubnet"]:::compute
  end
  subgraph spoke["Spoke VNet"]
    wl["workload subnet<br/>NSG: deny inbound internet"]:::compute
    pe["privateEndpoints subnet"]:::data
    rt["route 0.0.0.0/0 to firewall"]:::sec
  end
  law["Log Analytics"]:::obs
  hub <-->|peering| spoke
  wl -->|egress inspected| fw --> inet
  fw --> law
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

### Prod

```mermaid
flowchart LR
  inet["Internet"]:::edge
  admin["Operators"]:::edge
  subgraph hub["Hub VNet"]
    fw["Azure Firewall<br/>Premium — TLS + IDPS, zones 1-3"]:::edge
    bas["Azure Bastion"]:::edge
    gw["GatewaySubnet"]:::compute
  end
  subgraph spoke["Spoke VNet"]
    wl["workload subnet<br/>NSG: deny inbound internet"]:::compute
    pe["privateEndpoints subnet"]:::data
    rt["route 0.0.0.0/0 to firewall"]:::sec
  end
  law["Log Analytics<br/>90-day retention"]:::obs
  hub <-->|peering| spoke
  admin -->|RDP/SSH| bas --> wl
  wl -->|egress inspected| fw --> inet
  fw --> law
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

## Deploy

```bash
# Dev (Standard firewall, no Bastion)
az deployment group create -g <rg> -f main.bicep -p parameters/dev.bicepparam

# Prod (Premium firewall, Bastion, 90-day logs)
az deployment group create -g <rg> -f main.bicep -p parameters/prod.bicepparam
```

## Well-Architected highlights

- **Reliability** — firewall and public IPs are zone-redundant.
- **Security** — all spoke egress is inspected; inbound internet is denied; Bastion removes public IPs; Premium tier adds TLS inspection + IDPS.
- **Cost** — Bastion and Premium tier are prod-only.
- **Operations** — firewall logs/metrics flow to Log Analytics; rules live in a central policy.
- **Performance** — zone-redundant firewall scales with throughput.

## Cost estimate

```powershell
./tools/Get-CatalogCost.ps1 -UseCase hub-spoke-network
```
