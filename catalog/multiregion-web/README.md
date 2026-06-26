# Active-Active Multi-Region Web

A mission-critical global web footprint: **Azure Front Door Premium** with a managed
**WAF** load-balancing across **App Service** backends in two regions, backed by a
**multi-region Cosmos DB** with automatic failover.

## What this deploys

- **Front Door Premium** profile + endpoint + origin group with health probes.
- **WAF policy** (Microsoft DRS 2.1, Prevention) associated via a security policy.
- **App Service** plan + web app in a **primary** and a **secondary** region.
- Origin lockdown: each app accepts traffic **only from Front Door** (service tag `AzureFrontDoor.Backend` + `x-azure-fdid` header match).
- **Cosmos DB** replicated across both regions, automatic failover, optional multi-region writes, local auth disabled.
- Shared **Application Insights** + **Log Analytics**.

## Why this exists

Business-critical web workloads need to survive a regional outage and serve users with
low latency worldwide. This delivers an active-active footprint with a single global
anycast entry point, automated failover, and origin lockdown.

## Potential use cases

- Mission-critical customer portal with an availability SLO.
- Global e-commerce storefront needing low latency per geography.
- Regulated workload requiring regional resilience and DR.
- Public API needing global WAF and DDoS protection.

## Architecture

### Dev

```mermaid
flowchart LR
  user["Global users"]:::edge
  fd["Front Door Standard<br/>+ WAF — DRS 2.1"]:::edge
  subgraph p["Primary region — eastus"]
    app1["App Service<br/>1 instance"]:::compute
  end
  subgraph s["Secondary region — westus3"]
    app2["App Service<br/>1 instance"]:::compute
  end
  cosmos["Cosmos DB<br/>2 regions, auto-failover<br/>single-write"]:::data
  obs["App Insights + Log Analytics"]:::obs
  user --> fd
  fd -->|origin lockdown| app1
  fd -->|origin lockdown| app2
  app1 --> cosmos
  app2 --> cosmos
  app1 --> obs
  app2 --> obs
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

### Prod

```mermaid
flowchart LR
  user["Global users"]:::edge
  fd["Front Door Premium<br/>+ WAF — DRS 2.1"]:::edge
  subgraph p["Primary region — eastus"]
    app1["App Service<br/>3 zonal instances"]:::compute
  end
  subgraph s["Secondary region — westus3"]
    app2["App Service<br/>3 zonal instances"]:::compute
  end
  cosmos["Cosmos DB<br/>2 regions, auto-failover<br/>multi-region writes"]:::data
  obs["App Insights + Log Analytics"]:::obs
  user --> fd
  fd -->|origin lockdown| app1
  fd -->|origin lockdown| app2
  app1 --> cosmos
  app2 --> cosmos
  app1 --> obs
  app2 --> obs
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

## Deploy

```bash
# Dev (Standard Front Door, single instances)
az deployment group create -g <rg> -f main.bicep -p parameters/dev.bicepparam

# Prod (Premium Front Door, 3 zonal instances/region, multi-write Cosmos)
az deployment group create -g <rg> -f main.bicep -p parameters/prod.bicepparam
```

## Well-Architected highlights

- **Reliability** — active-active regions, Cosmos auto-failover, probe-driven routing.
- **Security** — managed WAF, origin lockdown to Front Door, HTTPS-only, keyless Cosmos.
- **Cost** — Standard Front Door + single instances in dev; multi-write only in prod.
- **Operations** — shared App Insights/Log Analytics; region surfaced to the app.
- **Performance** — anycast edge routing to the nearest healthy origin.

## Cost estimate

```powershell
./tools/Get-CatalogCost.ps1 -UseCase multiregion-web
```
