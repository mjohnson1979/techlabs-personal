# SME&C TechLabs

A catalog of templatized Azure use cases as Infrastructure as Code (Bicep), plus
a self-hosted gallery for browsing, costing, and deploying them. Every use case
is resilient by design, mapped to the Azure Well-Architected Framework, ships
with `dev` and `prod` parameter profiles, and carries machine-readable cost
metadata that is priced live against the public Azure Retail Prices API.

## Repository layout

| Path | What's there |
| --- | --- |
| `catalog/` | One folder per use case: `main.bicep`, `parameters/*.bicepparam`, `metadata.json`, `README.md`. Landing zones are nested (e.g. `catalog/landing-zone/core`). |
| `modules/` | Shared Bicep building blocks referenced by templates (Log Analytics, App Insights, Storage, Key Vault, private endpoints). |
| `site/` | The static gallery (`index.html` + `assets/`) that loads `site/catalog.json`. |
| `site/server/` | FastAPI backend that gates the gallery behind Entra ID and acts as the deploy control plane. See [site/server/README.md](site/server/README.md). |
| `tools/` | PowerShell utilities — `Build-Catalog.ps1` (regenerates `catalog.json` with live prices), `Get-CatalogCost.ps1` (per-use-case / portfolio cost), `Test-Catalog.ps1` (validation). |
| `llms.txt` | Machine-readable overview of the catalog for LLM tooling. |

## How the catalog is organized

- Each use case lives under `catalog/<use-case>/` and contains a deployable
  `main.bicep`, `dev`/`prod` parameter files, a `metadata.json` contract, and a `README.md`.
- `metadata.json` is the contract: `what`, `why`, `potentialUseCases`, `category`,
  `industries`, `compliance`, `wellArchitected` (5 pillars), and `cost.components`
  (each `fixed` or `usage`). The gallery and the cost tooling both read it.

## The gallery and backend

- The gallery is a static site (`site/`) served only to signed-in users. The
  backend in `site/server/` runs an Entra ID OAuth (PKCE) gate, records activity,
  and provisions/destroys Azure resources with its own managed identity.
- A single **SQLite** file (on App Service's persistent `/home` share) is the
  system of record for users, roles, deployments, and telemetry. Full schema and
  rationale: [site/server/README.md](site/server/README.md).

## Cost estimation

- `tools/Get-CatalogCost.ps1 -UseCase <path>` prices one use case; `-All` rolls up
  the portfolio. `tools/Build-Catalog.ps1` bakes live prices into `site/catalog.json`.
- Prices come from `https://prices.azure.com/api/retail/prices` (public, no auth).
  Components are `fixed` (committed monthly) or `usage` (illustrative, from assumed
  monthly quantities). Each card shows an estimated monthly cost with an itemized
  breakdown in the detail view.

## Validation

- `.github/workflows/bicep-validate.yml` builds and lints every `main.bicep`,
  module, and `.bicepparam` on PR/push.
- Locally: `az bicep build --file <template>` and
  `az bicep build-params --file <paramfile>`, or `tools/Test-Catalog.ps1`.

## Tailoring for regulated industries

Landing zones are the same across industries; regulated differences
(HLS/HIPAA-HITRUST, FSI, manufacturing, automotive) come from a hardening overlay
— private endpoints, customer-managed keys, diagnostic settings, and Azure Policy
— opted into via parameters and the `private-endpoint` module, not by forking
templates. The `industries` and `compliance` arrays in each `metadata.json` drive
the gallery filters.
