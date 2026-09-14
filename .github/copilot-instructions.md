# Deployment Portal - Customer Branch Instructions

Customer-operated Azure deployment platform: vanilla JavaScript UI and FastAPI
control plane. This branch contains no bundled workload or hosting IaC.

## Layout

- `site/`: runtime, with `main.py`, `assets/`, and the `server/` backend.
- `site/requirements.txt`: the only runtime dependency declaration.
- `site/*.json`: empty customer snapshots and the customer distribution marker.
- `site/server/tests/`: offline tests with temporary databases and mocked cloud calls.
- `tools/build_customer_distribution.py`: history-free allowlist export and source seed.
- `tools/preview_customer.py`: loopback-only, read-only synthetic preview.
- `customer/README.md`: customer setup and operations guide.
- `customer/RELEASE-CHECKLIST.md`: development-only release gates.

## Boundaries

- Do not restore bundled catalogs, internal resource IDs, branding, workflows,
  compiled ARM templates or Terraform samples. Customers supply their own IaC.
- The existing Git history is not customer-safe. Publish only an approved export
  into a new repository, never a fork or mirror of this working repository.
- Preserve the MIT copyright notice. Technical service names are not product branding.
- Hosting requires customer-owned App Service Authentication and managed identity;
  the local preview is not a production authentication bypass.
- First-run installation may precede the primary Entra connection only with the
  explicitly enabled, expiring setup-code installer. Easy Auth stays enabled;
  normal APIs/jobs remain locked until same-browser Entra verification completes.
  Never claim an installation for the user or complete their connection for them.
- After installation, Setup is admin-only. Subscriptions use ID-only input and Azure metadata lookup.
  No quota checks during initial setup; use the workload view after setup.
- GitHub setup uses customer-owned OAuth device authorization. Never reuse a
  publisher application's client ID or expose tokens to the browser or logs.
- Secrets are configured via protected host settings or verified customer Key Vault.
- One worker and one instance are currently required by the database and scheduler.

## Validation

- Use Windows PowerShell-compatible commands and the root `.venv`.
- Run `python -m pytest site/server/tests`. Do not use live cloud data as test fixtures.
- Check touched JavaScript with `node --check`. Preview checks are desktop-only.
- Keep generated archives under ignored `runs/`. Do not ship virtual environments,
  local databases, logs, caches, test files or development instructions.
- No workflow currently deploys this branch. Do not commit, push, publish or modify
  shared cloud infrastructure without explicit authorization.
