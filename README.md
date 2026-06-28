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
| `site/` | The static gallery (`index.html` + `assets/`) that loads `site/catalog.json`, plus the `site/capacity.json` and `site/updates.json` signal snapshots. |
| `site/server/` | FastAPI backend that gates the gallery behind Entra ID and acts as the deploy control plane. See [site/server/README.md](site/server/README.md). |
| `tools/` | PowerShell utilities — `Build-Catalog.ps1` (regenerates `catalog.json` with live prices), `Get-CatalogCost.ps1` (per-use-case / portfolio cost), `Test-Catalog.ps1` (validation), `Build-Capacity.ps1` (region capacity snapshot), `Build-Updates.ps1` (Azure service lifecycle snapshot). Plus `new_use_case_from_issue.py`, which scaffolds a catalog entry from a submission issue. |
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

## Live signals (capacity & service lifecycle)

The gallery surfaces two refreshed-on-a-schedule signals alongside each use case,
each a static JSON snapshot committed back to the repo so the site stays
fully static:

- **Region capacity** — `tools/Build-Capacity.ps1` probes subscription quota and
  regional availability for capacity-sensitive services (Azure OpenAI TPM, App
  Service, VM cores, Container Apps) and writes `site/capacity.json`. Cards show a
  **"Quota-sensitive"** flag, the region picker is colour-coded
  (green/yellow/red), and the **Capacity** page renders a service x region grid.
  Refreshed daily by `.github/workflows/capacity.yml` (Azure OIDC).
- **Service lifecycle** — `tools/Build-Updates.ps1` reads the public Microsoft
  Release Communications feed behind [azure.microsoft.com/updates](https://azure.microsoft.com/updates)
  (no Azure auth), maps each update's products onto the services a use case
  provisions, and writes `site/updates.json`. The modal's **"Azure roadmap &
  retirements"** section lists upcoming retirements, new previews, and recent GA,
  and cards flag use cases with an upcoming retirement. Refreshed weekly by
  `.github/workflows/updates.yml` (no secrets required).

## Validation

- `.github/workflows/bicep-validate.yml` builds and lints every `main.bicep`,
  module, and `.bicepparam` on PR/push.
- Locally: `az bicep build --file <template>` and
  `az bicep build-params --file <paramfile>`, or `tools/Test-Catalog.ps1`.

## Deployment

- `.github/workflows/deploy-gallery.yml` compiles the templates and publishes the
  `site/` gallery to App Service on every push to `main` that touches `site/`,
  `catalog/`, or `tools/Build-Templates.ps1`. It authenticates with the
  `id-techlabs-deployer` user-assigned identity via **Azure OIDC** (basic-auth
  publishing is disabled on the web app), using the `AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` repository variables.

## Submitting a use case

Anyone can propose a use case straight from the gallery — no code required:

1. Click **"+ Submit a use case"** on the site and describe the scenario and the
   Azure services it uses. The form opens a pre-filled GitHub issue (the
   `.github/ISSUE_TEMPLATE/use-case-submission.yml` form); you review and submit
   it on GitHub. The gallery stores **no** GitHub credential.
2. `.github/workflows/use-case-intake.yml` picks up the labelled issue, runs
   `tools/new_use_case_from_issue.py` to scaffold `catalog/<slug>/metadata.json`
   (+ `README.md` and a `main.bicep` stub), regenerates `site/catalog.json`, and
   opens a **draft pull request**. It is secretless — only the built-in
   `GITHUB_TOKEN`.
3. A maintainer authors the Bicep and parameters, fills in the Well-Architected
   pillars / quota / priced cost components, and merges. The deploy workflow then
   publishes the new use case to the gallery.

## Tailoring for regulated industries

Landing zones are the same across industries; regulated differences
(HLS/HIPAA-HITRUST, FSI, manufacturing, automotive) come from a hardening overlay
— private endpoints, customer-managed keys, diagnostic settings, and Azure Policy
— opted into via parameters and the `private-endpoint` module, not by forking
templates. The `industries` and `compliance` arrays in each `metadata.json` drive
the gallery filters.
