# Event-Driven Microservices (Container Apps + Dapr)

A keyless, event-driven microservices topology on **Azure Container Apps** with **Dapr**:
**Service Bus** pub/sub, a **Cosmos DB** Dapr state store, an external API, an internal
worker, and **KEDA** queue-depth autoscaling.

## What this deploys

- **Container Apps environment** with Dapr enabled and App Insights telemetry.
- **Dapr components** (scoped to both apps):
  - `statestore` → Cosmos DB (`state.azure.cosmosdb`).
  - `orderpubsub` → Service Bus topic (`pubsub.azure.servicebus.topics`).
- **API container app** — external HTTPS ingress, concurrency-based HTTP scaling.
- **Worker container app** — no ingress, **KEDA** Service Bus scaler (keyless via identity).
- **Service Bus** (Standard/Premium, local auth disabled) with an `orders` topic + dead-lettering subscription.
- **Cosmos DB** (serverless, keyless) state store.
- **User-assigned managed identity** shared by both apps, with Service Bus Data Owner + Cosmos data-plane roles.
- **Log Analytics** + **Application Insights**.

## Why this exists

Microservices teams want async messaging, durable state, and event-driven autoscaling
without running Kubernetes or managing broker credentials. Container Apps + Dapr provide
the building-block APIs; Service Bus and Cosmos supply the backbone; managed identity
removes every connection string.

## Potential use cases

- Order processing / saga workflows with a worker pool.
- Decoupled command and event services behind a single gateway.
- Burst-scaling background processing off a queue.
- Polyglot microservices needing portable state and pub/sub.

## Architecture

### Dev

```mermaid
flowchart LR
  user["Clients"]:::edge
  subgraph env["Container Apps environment + Dapr"]
    api["api app<br/>external ingress, scale-to-zero"]:::compute
    worker["worker app<br/>KEDA Service Bus scaler"]:::compute
  end
  sb["Service Bus — Standard<br/>orders topic, local auth off"]:::data
  cosmos["Cosmos DB serverless<br/>Dapr state store"]:::data
  mi["User-assigned identity<br/>SB Data Owner + Cosmos"]:::sec
  obs["App Insights + Log Analytics"]:::obs
  user -->|HTTPS| api
  api -->|pub via Dapr| sb
  sb -->|sub| worker
  api --> cosmos
  worker --> cosmos
  mi -.-> api
  mi -.-> worker
  api --> obs
  worker --> obs
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

### Prod

```mermaid
flowchart LR
  user["Clients"]:::edge
  subgraph env["Container Apps environment + Dapr — zone-redundant"]
    api["api app<br/>external ingress, warm replicas"]:::compute
    worker["worker app<br/>KEDA Service Bus scaler"]:::compute
  end
  sb["Service Bus — Premium, zone-redundant<br/>orders topic, local auth off"]:::data
  cosmos["Cosmos DB serverless<br/>Dapr state store"]:::data
  mi["User-assigned identity<br/>SB Data Owner + Cosmos"]:::sec
  obs["App Insights + Log Analytics"]:::obs
  user -->|HTTPS| api
  api -->|pub via Dapr| sb
  sb -->|sub| worker
  api --> cosmos
  worker --> cosmos
  mi -.-> api
  mi -.-> worker
  api --> obs
  worker --> obs
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

## Deploy

```bash
# Dev (scale-to-zero, Standard Service Bus)
az deployment group create -g <rg> -f main.bicep -p parameters/dev.bicepparam

# Prod (warm replicas, Premium Service Bus, zone-redundant)
az deployment group create -g <rg> -f main.bicep -p parameters/prod.bicepparam
```

## Well-Architected highlights

- **Reliability** — zone-redundant environment, Premium Service Bus, dead-lettering.
- **Security** — local auth disabled, shared least-privilege identity, HTTPS ingress.
- **Cost** — scale-to-zero + Standard broker in dev; KEDA scaling; serverless Cosmos.
- **Operations** — Dapr telemetry to App Insights; broker/store abstracted by Dapr.
- **Performance** — event-driven autoscale; concurrency-based HTTP scaling.

## Cost estimate

```powershell
./tools/Get-CatalogCost.ps1 -UseCase microservices-aca
```
