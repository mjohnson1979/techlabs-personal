# AI Chat (Azure OpenAI + App Service + Cosmos DB)

A chat API on App Service that calls an Azure OpenAI **gpt-5-mini** deployment
and persists conversations in **serverless Cosmos DB**. Access is **keyless**:
the web app's managed identity authenticates to both Azure OpenAI and Cosmos,
and local/key auth is disabled on both.

## Why this exists

Conversational AI is the most requested GenAI pattern. This wires the three pieces
every chatbot needs - a model endpoint, a stateless app tier, and durable
conversation storage - with keyless security and pay-per-use billing that matches
spiky chat traffic.

## Potential use cases

- A customer-support or internal helpdesk chatbot
- A product/document Q&A assistant (extend with retrieval from the AI landing zone)
- A copilot embedded in an existing web app
- A GenAI prototype you harden to production with the prod profile

## What gets deployed

| Component | Purpose | Module |
| --- | --- | --- |
| Log Analytics workspace | Central log store | [log-analytics](../../modules/log-analytics/main.bicep) |
| Application Insights | App monitoring | [app-insights](../../modules/app-insights/main.bicep) |
| Azure OpenAI account + deployment | gpt-5-mini chat model | inline |
| Cosmos DB (serverless) + container | Conversation history | inline |
| App Service plan + Web App | Chat API with managed identity | inline |

## Well-Architected posture

- **Reliability** — zone-redundant app plan in prod; Cosmos serverless with Session consistency; stateless app tier.
- **Security** — keyless managed-identity access to OpenAI and Cosmos (local auth disabled), TLS 1.2, HTTPS-only.
- **Cost Optimization** — Cosmos serverless (pay per RU) and PAYG token billing suit spiky chat load; cheap dev profile.
- **Operational Excellence** — centralized telemetry, parameterized model/version/TPM, IaC dev/prod params.
- **Performance Efficiency** — provisioned TPM per environment, autoscaling serverless Cosmos, Premium v3 compute in prod.

See [metadata.json](metadata.json) for the machine-readable WAF + cost declaration.

> **Region note:** Azure OpenAI model availability varies by region. Pick a
> `location` that offers `gpt-5-mini`.
>
> **After you deploy:** grant the web app's managed identity data-plane roles —
> `Cognitive Services OpenAI User` on the OpenAI account and a Cosmos DB SQL
> data role (e.g. *Cosmos DB Built-in Data Contributor*) on the account.

## Quota & capacity

This use case is **quota-sensitive** — it cannot deploy into a region/subscription
that lacks capacity for its core services:

| Service | Needs | Check |
| --- | --- | --- |
| Azure OpenAI (`gpt-5-mini`) | Model offered in the region + available TPM quota | [Quotas blade](https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub/~/Quotas) |
| Azure App Service (Linux plan) | Compute quota in the region | [App Service quotas](https://portal.azure.com/#view/Microsoft_Azure_Capacity/QuotaMenuBlade/~/myQuotas) |

The gallery surfaces this live: each capacity-sensitive card shows a **"Quota-sensitive"**
badge, the deploy dialog has a **color-coded region picker** (green = can host,
yellow = may hit constraints, red = no capacity for at least one service), and the
**Capacity** page in the header shows a service × region grid. That data is a daily
snapshot produced by [`tools/Build-Capacity.ps1`](../../tools/Build-Capacity.ps1)
(written to `site/capacity.json`). If every candidate region is red, split the
workload across regions (multi-region deployment) or request a quota increase.

## Parameters

| Name | Default | Description |
| --- | --- | --- |
| `location` | resource group location | Azure region (must offer the model) |
| `environment` | `dev` | Environment name |
| `prefix` | `techlabs` | Resource name prefix |
| `appServiceSku` | `B1` | App plan SKU (P1v3+ for zone redundancy) |
| `appCapacity` | `1` | App plan instance count |
| `appZoneRedundant` | `false` | Zone-redundant app plan |
| `chatModelName` | `gpt-5-mini` | Model to deploy |
| `chatModelVersion` | `2025-08-07` | Model version |
| `chatModelSku` | `GlobalStandard` | Model deployment SKU |
| `chatModelCapacity` | `10` | TPM capacity (thousands) |

## Architecture

### Dev

```mermaid
flowchart LR
  user["Users"]:::edge
  app["Chat API on App Service<br/>B1, managed identity"]:::compute
  oai["Azure OpenAI<br/>gpt-5-mini — keyless"]:::data
  cosmos["Cosmos DB serverless<br/>chat history — keyless"]:::data
  obs["App Insights + Log Analytics"]:::obs
  user -->|HTTPS| app
  app -->|token| oai
  app --> cosmos
  app --> obs
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

### Prod

```mermaid
flowchart LR
  user["Users"]:::edge
  app["Chat API on App Service<br/>P1v3, zone-redundant"]:::compute
  oai["Azure OpenAI<br/>gpt-5-mini — higher TPM, keyless"]:::data
  cosmos["Cosmos DB serverless<br/>chat history — keyless"]:::data
  obs["App Insights + Log Analytics"]:::obs
  user -->|HTTPS| app
  app -->|token| oai
  app --> cosmos
  app --> obs
  classDef edge fill:#ffe8cc,stroke:#d9480f,color:#000
  classDef compute fill:#d0ebff,stroke:#1971c2,color:#000
  classDef data fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef sec fill:#f3d9fa,stroke:#9c36b5,color:#000
  classDef obs fill:#f1f3f5,stroke:#868e96,color:#000
```

## Deploy

```powershell
az group create --name rg-techlabs-aichat --location eastus

# Dev
az deployment group create `
  --resource-group rg-techlabs-aichat `
  --template-file ./main.bicep `
  --parameters ./parameters/dev.bicepparam

# Prod (zone-redundant, higher TPM)
az deployment group create `
  --resource-group rg-techlabs-aichat `
  --template-file ./main.bicep `
  --parameters ./parameters/prod.bicepparam
```

## Estimate cost

```powershell
./tools/Get-CatalogCost.ps1 -UseCase ai-chat
```

## Clean up

```powershell
az group delete --name rg-techlabs-aichat --yes --no-wait
```
