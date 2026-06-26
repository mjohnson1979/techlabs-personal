# Landing Zones

Foundational platforms that workloads build on top of. Deploy a landing zone
first, then layer use cases into the same resource group (or peer to it).

| Landing zone | What it provides |
| --- | --- |
| [core](core/) | Log Analytics, hardened Storage, Key Vault, hub VNet + NSG — the baseline every workload needs |
| [ai](ai/) | Everything for GenAI: Azure AI Services (Foundry), Azure AI Search, Key Vault, Storage, Log Analytics — keyless |

## Which one?

- Building a **general app or API**? Start with **core**, then add a use case like
  [web-app](../web-app/) or [sql-data-api](../sql-data-api/).
- Building a **GenAI app** (chat, RAG, agents)? Start with **ai**, then add a use
  case like [ai-chat](../ai-chat/).

Landing zones are intentionally similar across industries — the differences for
regulated industries (HLS, FSI, manufacturing, automotive) come from the
**hardening overlay** (private endpoints, customer-managed keys, policy), not from
forking the landing zone. See the *Tailoring for regulated industries* section in
the [root README](../../README.md).
