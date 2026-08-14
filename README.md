# SME&C TechLabs

A catalog of templatized Azure use cases as Infrastructure as Code (**Bicep and
Terraform**), plus a self-hosted gallery for browsing, costing, and deploying them.
Every use case is resilient by design, mapped to the Azure Well-Architected
Framework, ships with `dev` and `prod` parameter profiles, and carries
machine-readable cost metadata that is priced live against the public Azure Retail
Prices API.

## Why Tech Labs exists

**The problem.** Cloud value is realized at the moment a workload runs in
production — yet most of the elapsed time between "we've decided to build on Azure"
and "it's live" is spent not building, but re-deciding. Every team rediscovers the
same architecture, re-estimates the same costs, re-litigates the same security and
compliance posture, and rebuilds the same deployment plumbing. That effort is
duplicated across engagements, rarely captured, and inconsistently aligned to Azure
best practice. The result is slow time-to-value, uneven quality, and unpredictable
spend — the friction that keeps pipeline from converting into consumption.

**The approach.** Tech Labs is a curated catalog of **production-shaped Azure use
cases** — reference workloads that are architected, costed, and ready to deploy and validated
by the CSA team.
Each one encodes the decisions a team would otherwise make from scratch, so the
starting point is already resilient, compliant, and priced. It converts
institutional knowledge into reusable, governed assets instead of one-off scripts
that leave with the people who wrote them.

**The outcome.** Teams move from intent to a running, well-architected footprint in
the same day rather than over weeks — with cost known up front, best practice built
in, and a consistent baseline every project inherits.

### Business value

- **Faster time-to-value.** Compress weeks of design, estimation, and scaffolding
  into a same-day deployment — accelerating proofs of concept and shortening the
  path from decision to production consumption.
- **Lower delivery risk.** Every workload is resilient by design and mapped to the
  five pillars of the Azure **Well-Architected Framework**, so reliability,
  security, operations, and performance are the default — not a post-hoc remediation.
- **Cost certainty.** Live pricing from the public Azure Retail Prices API and
  portfolio roll-ups put a defensible monthly estimate in front of stakeholders
  *before* the first resource is created, with separate `dev`/`prod` profiles.
- **Governance and consistency at scale.** Best practice is codified once and
  reused everywhere, so quality no longer depends on which individual built it.
  Use cases carry category, industry, and compliance context, plus live region
  capacity/quota and Azure service-lifecycle signals — so what you choose will
  actually deploy where you need it.
- **Standardization without lock-in.** Every use case ships both **Bicep** and
  **Terraform**, so organizations adopt Tech Labs on the IaC standard they already run.

### How it accelerates workloads to Azure

- **Browse to deployed, securely.** A self-hosted, Entra ID–gated gallery deploys a
  use case with its own managed identity — no secrets, no hand-built scripts — or
  hands teams the exact `az`/`terraform` CLI to run in their own pipelines.
- **A launchpad, not a demo.** Templates are parameterized, documented, and meant to
  be extended into real solutions — so the first deployment is the foundation of the
  build, not throwaway scaffolding.
- **Compounding leverage.** Each engagement that starts from Tech Labs reinforces the
  same resilient, compliant, cost-aware baseline — shrinking the distance from idea to
  a supported Azure footprint and helping customers land workloads faster, repeatably,
  and with confidence.

## Repository layout

| Path | What's there |
| --- | --- |
| `catalog/` | One folder per use case: `main.bicep`, `parameters/*.bicepparam`, a parallel `terraform/` module, `metadata.json`, `README.md`. Landing zones are nested (e.g. `catalog/landing-zone/core`). |
| `modules/` | Shared Bicep building blocks referenced by templates (Log Analytics, App Insights, Storage, Key Vault, private endpoints). |
| `site/` | The static gallery (`index.html` + `assets/`) that loads `site/catalog.json`, plus the `site/capacity.json`, `site/registry.json`, and `site/updates.json` snapshots. |
| `site/server/` | FastAPI backend that gates the gallery behind Entra ID and acts as the deploy control plane. See [site/server/README.md](site/server/README.md). |
| `tools/` | PowerShell utilities — `Build-Catalog.ps1` (regenerates `catalog.json` with live prices), `Get-CatalogCost.ps1` (per-use-case / portfolio cost), `Test-Catalog.ps1` (validation), `Build-Capacity.ps1` (region capacity snapshot), `Build-Registry.ps1` (model/SKU/region registry snapshot), `Build-Updates.ps1` (Azure service lifecycle snapshot), `Build-Templates.ps1` (compiles catalog templates for deploy). |
| `llms.txt` | Machine-readable overview of the catalog for LLM tooling. |

## How the catalog is organized

- Each use case lives under `catalog/<use-case>/` and contains a deployable
  `main.bicep`, `dev`/`prod` parameter files, a `metadata.json` contract, and a `README.md`.
- It also ships a parallel **Terraform** module in `catalog/<use-case>/terraform/`
  (`main.tf`, `variables.tf`, `outputs.tf`, `providers.tf`, `dev.tfvars`,
  `prod.tfvars`) that provisions the same resources. The gallery surfaces both
  engines; see [site/server/README.md](site/server/README.md#deployment-engines-bicep-and-terraform).
- `metadata.json` is the contract: `what`, `why`, `potentialUseCases`, `category`,
  `industries`, `compliance`, `wellArchitected` (5 pillars), and `cost.components`
  (each `fixed` or `usage`). The gallery and the cost tooling both read it.

## The gallery and backend

- The gallery is a static site (`site/`) served only to signed-in users. The
  backend in `site/server/` runs an Entra ID OAuth (PKCE) gate, records activity,
  and provisions/destroys Azure resources with its own managed identity. Every
  deployment carries an auto-cleanup end time (default 2 days, max 4); a
  background scheduler tears down expired deployments, and a failed deploy that
  left partial resources can be cleaned up from the account/admin views.
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
- **Advanced deployment options + live re-estimate.** The deploy panel groups a
  use case's template parameters into per-service boxes (models, SKUs, sizes,
  capacities) sourced from `site/registry.json`. Changing the region or any option
  calls `POST /api/estimate`, which reprices the affected components against the
  live retail API — so the itemized total reflects the chosen SKU, instance
  capacity, node/replica counts, zone redundancy, and SQL tier before you deploy.

## Live signals (capacity & service lifecycle)

The gallery surfaces two refreshed-on-a-schedule signals alongside each use case,
each a static JSON snapshot committed back to the repo so the site stays
fully static:

- **Region capacity** — `tools/Build-Capacity.ps1` probes subscription quota and
  regional availability across the Americas regions for capacity-sensitive
  services (Azure OpenAI TPM per model/version/SKU, App Service, VM cores and
  per-family quota, Container Apps, AVS, Fabric, Power BI, Purview) and writes
  `site/capacity.json`. Cards show a **"Quota-sensitive"** flag; the region picker
  is colour-coded (green/yellow/red) and reacts to the model/VM size chosen in
  advanced options; and the **Capacity** page groups the signal into collapsible
  per-service items. Refreshed daily by `.github/workflows/capacity.yml` (Azure
  OIDC). The companion model/SKU/region catalogue in `site/registry.json` — which
  powers the advanced-option dropdowns — is refreshed by
  `.github/workflows/registry.yml`.
- **Service lifecycle** — `tools/Build-Updates.ps1` reads the public Microsoft
  Release Communications feed behind [azure.microsoft.com/updates](https://azure.microsoft.com/updates)
  (no Azure auth), maps each update's products onto the services a use case
  provisions, and writes `site/updates.json`. A dedicated **Roadmap &
  retirements** page (header nav) lets you search by service and expand
  collapsible per-service lists of upcoming retirements, new previews, and recent
  GA — with counts that always match the items shown. A use case's detail modal
  surfaces only the retirements affecting *its* services and links to the full
  page. Refreshed weekly by `.github/workflows/updates.yml` (no secrets required).

## Validation

- `.github/workflows/bicep-validate.yml` builds and lints every `main.bicep`,
  module, and `.bicepparam` on PR/push.
- `.github/workflows/terraform-validate.yml` runs `terraform fmt -check` and
  `terraform validate` on every `catalog/**/terraform` module on PR/push.
- Locally: `az bicep build --file <template>` and
  `az bicep build-params --file <paramfile>`, or `tools/Test-Catalog.ps1`. For
  Terraform: `terraform fmt` then `terraform init -backend=false` + `terraform
  validate` inside a module folder.

## Deployment

- `.github/workflows/deploy-gallery.yml` compiles the templates and publishes the
  `site/` gallery to App Service on every push to `main` that touches `site/`,
  `catalog/`, or `tools/Build-Templates.ps1`. It authenticates with the
  `id-techlabs-deployer` user-assigned identity via **Azure OIDC** (basic-auth
  publishing is disabled on the web app), using the `AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` repository variables.

## Submitting a use case

The gallery's **"+ Submit a use case"** panel accepts a **zip upload** of a
ready-to-deploy template — the submitter brings working Bicep, so what lands is
deployable, not a stub:

1. Upload a `.zip` containing `main.bicep` at the root and at least one
   `parameters/<env>.bicepparam` (optional `metadata.json` / `README.md`).
2. `POST /api/use-cases/upload` validates the archive (zip-slip guard, size and
   decompression-bomb limits, allowed extensions, required files present),
   normalizes it into the `catalog/<slug>/` layout — synthesizing
   `metadata.json` / `README.md` when absent — and stages it server-side.
3. When a `GALLERY_GITHUB_TOKEN` (with `contents: write`) is configured, the
   backend creates a review **branch** and commits the bundle via the GitHub Git
   Data API; otherwise the submission stays staged for manual download. Either
   way it appears in the admin **Submissions** tab.
4. A maintainer reviews the staged entry, opens the pull request via the
   pre-filled **compare link** (org policy blocks Actions from opening PRs
   directly), and merges. The deploy workflow then publishes the new use case.

A natural-language path that produces *deployable* templates (compose from Azure
Verified Modules, match to an existing template, or a build/lint/what-if auto-fix
loop) is tracked in [issue #7](https://github.com/mcaps-microsoft/TechLabs/issues/7).
The earlier AI-drafted-prose flow was parked because its Bicep was not deployable
without a maintainer authoring the real infrastructure.

## Tailoring for regulated industries

Landing zones are the same across industries; regulated differences
(HLS/HIPAA-HITRUST, FSI, manufacturing, automotive) come from a hardening overlay
— private endpoints, customer-managed keys, diagnostic settings, and Azure Policy
— opted into via parameters and the `private-endpoint` module, not by forking
templates. The `industries` and `compliance` arrays in each `metadata.json` drive
the gallery filters.
