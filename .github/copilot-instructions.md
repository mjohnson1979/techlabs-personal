# Tech Labs — Copilot repository instructions

Azure use-case gallery: a static front end plus a FastAPI control-plane backend, with
Bicep templates for each use case and PowerShell + GitHub Actions automation.

## Layout

- `site/` — deployed web app. Static UI in `site/assets/` (vanilla JS), FastAPI entry point
  `site/main.py`, backend package `site/server/`. Data snapshots: `site/catalog.json`,
  `site/updates.json` (service lifecycle), `site/capacity.json` (region capacity).
- `catalog/` — per-use-case Bicep templates (`main.bicep` + parameters).
- `tools/` — PowerShell build/validation scripts (`Build-Catalog.ps1`, `Build-Updates.ps1`,
  `Build-Capacity.ps1`, `Build-Templates.ps1`, `Test-Catalog.ps1`, `Get-CatalogCost.ps1`).
- `.github/workflows/` — `deploy-gallery.yml` (App Service deploy via OIDC), `updates.yml`
  (weekly), `capacity.yml` (daily), `bicep-validate.yml`, use-case deploy/destroy/intake.
- `.github/instructions/` — language/domain best-practice instruction files (auto-applied by `applyTo`).

## Critical gotchas

- **Two requirements files.** The deploy (Oryx/CI) installs from `site/requirements.txt`.
  `site/server/requirements.txt` is NOT used by deploy. Any dependency the app imports at
  boot (e.g. `python-multipart` for upload routes) MUST be in `site/requirements.txt`.
- **Hosting is Azure App Service** (Linux, `PYTHON|3.12`, gunicorn + uvicorn worker), not
  GitHub Pages. Deploy is triggered only by `deploy-gallery.yml` on pushes to `main` that
  touch `site/**`, `catalog/**`, or `tools/Build-Templates.ps1`.
- `AzureIcons.iconFor(key)` in the front end returns an **SVG string**, not a DOM node —
  render it with the `html:` prop, never `appendChild`/`cloneNode`.

## Working in this repo (Windows PowerShell)

- Shell is Windows PowerShell 5.1. Chain commands with `;` — never `&&`. Keep scripts ASCII-only.
- Activate the venv before Python work: `.\.venv\Scripts\Activate.ps1`.
- Backend tests: `python -m pytest site/server/tests`. Bicep: `az bicep build --file <file>`.
- `git push` writes progress to stderr (looks like an error but succeeds) — verify with
  `git log --oneline -1 origin/main`.
- Take reversible actions freely; confirm before destructive or shared-infra changes.
