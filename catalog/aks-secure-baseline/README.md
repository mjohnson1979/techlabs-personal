# AKS Secure Baseline

A private **AKS** cluster codifying the Microsoft AKS secure-baseline guidance: private
API server, no local accounts, Entra + Azure RBAC, workload identity, managed-identity
image pulls, and Container Insights.

## What this deploys

- **Virtual network** with `aks` and `privateEndpoints` subnets.
- **Private AKS cluster** with:
  - Azure CNI **overlay** + **Cilium** dataplane and network policy.
  - **Local accounts disabled**, Entra-managed AAD profile with **Azure RBAC**.
  - **OIDC issuer** + **workload identity** enabled.
  - **Key Vault secrets provider** addon (with rotation).
  - **Container Insights** (oms agent) to Log Analytics.
  - Zone-spread system node pool with cluster autoscaler.
- **Premium Azure Container Registry** (admin user disabled).
- **AcrPull role assignment** for the cluster's kubelet managed identity.

## Why this exists

Hand-rolled Kubernetes security drifts quickly. This template starts every cluster
compliant — private control plane, no local/secret credentials, identity-based image
pulls, and pod-level network segmentation.

## Potential use cases

- Standardized landing zone for microservices platforms.
- Regulated container workloads needing a private API server.
- Multi-team cluster with Azure RBAC namespace isolation.
- Workload-identity-based access to Key Vault, SQL, and storage.

## Architecture

### Dev

```mermaid
flowchart LR
  ops["Operators"]:::edge
  subgraph vnet["Virtual Network"]
    subgraph aks["AKS — public API, Free tier"]
      np["system node pool<br/>1 node, AzureLinux"]:::compute
    end
    pe["privateEndpoints subnet"]:::data
  end
  acr["Premium ACR<br/>admin disabled"]:::data
  law["Container Insights to Log Analytics"]:::obs
  ops -->|kubectl, Entra + RBAC| aks
  np -->|AcrPull via kubelet MI| acr
  aks --> law
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

### Prod

```mermaid
flowchart LR
  ops["Operators"]:::edge
  subgraph vnet["Virtual Network"]
    subgraph aks["AKS — private API, Standard SLA"]
      np["system node pool<br/>3 zonal nodes + autoscaler"]:::compute
    end
    pe["privateEndpoints subnet"]:::data
  end
  acr["Premium ACR<br/>admin disabled"]:::data
  law["Container Insights to Log Analytics"]:::obs
  ops -->|via VNet / Bastion| aks
  np -->|AcrPull via kubelet MI| acr
  aks --> law
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

## Deploy

```bash
# Dev (public API, single node, Free tier)
az deployment group create -g <rg> -f main.bicep -p parameters/dev.bicepparam

# Prod (private API, 3 zonal nodes, Standard tier)
az deployment group create -g <rg> -f main.bicep -p parameters/prod.bicepparam
```

> A private cluster's API server is only reachable from within the VNet (or a peered
> network / Bastion host). Pair this with [hub-spoke-network](../hub-spoke-network/README.md).

## Well-Architected highlights

- **Reliability** — zonal node pool, Standard uptime SLA, autoscaler.
- **Security** — private API, no local accounts, workload identity, AcrPull, Cilium policy.
- **Cost** — Free tier + single node in dev; autoscale in prod.
- **Operations** — Container Insights, secret rotation, surge upgrades.
- **Performance** — Cilium eBPF dataplane; overlay networking.

## Cost estimate

```powershell
./tools/Get-CatalogCost.ps1 -UseCase aks-secure-baseline
```
