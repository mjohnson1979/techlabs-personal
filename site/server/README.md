# Techlabs gallery backend

The backend is a small FastAPI app that does two jobs:

1. **Gates the static gallery behind Microsoft Entra ID** — only signed-in users
   from your tenant can see `site/index.html`, the assets, and `catalog.json`.
2. **Acts as the control plane and executor** — it records who deploys what,
   provisions Azure resources directly with its managed identity, and tears them
   down on a schedule.

A single **SQLite** file is the system of record. There is no external database
to run or pay for; the file lives on App Service's persistent storage and
survives restarts and redeploys.

## Modules

| File | Responsibility |
| --- | --- |
| `app.py` | FastAPI app; serves the static gallery, runs the OAuth gate, starts the scheduler. |
| `entra_auth.py` | Entra ID OAuth (PKCE) sign-in flow and the in-memory session store. |
| `identity.py` | Resolves the caller's identity + role for `/api/*` routes (fails closed). |
| `api.py` | The `/api/*` JSON endpoints (deployments, users, telemetry). |
| `db.py` | **All persistence.** SQLite schema + every read/write helper. |
| `azure_deploy.py` | In-process executor that provisions/destroys Azure resources with the app's managed identity by default. External Bicep deployments use the signed-in user's delegated ARM token instead; Terraform remains managed-identity-only. |
| `terraform_exec.py` | Terraform engine: runs a use case's Terraform module with the managed identity (azurerm + `ARM_USE_MSI`) instead of submitting ARM JSON. |
| `terraform_bootstrap.py` | Opt-in, best-effort provisioning of the `terraform` binary into the persistent `/home` share on startup. |
| `scheduler.py` | Background task that tears down deployments whose end time has passed. |

## The database

### Where it lives

`db._default_db_path()` picks the location in this order:

1. `GALLERY_DB_PATH` environment variable, if set (used for local dev and tests).
2. `/home/data/techlabs.db` — App Service's **persistent Azure Files share**, when
   `/home` is writable. This survives restarts, scale events, and redeploys.
3. `site/server/data/techlabs.db` — a local file next to the package (local dev
   fallback).

The connection opens once per process, uses **WAL journaling** for concurrent
reads, and is guarded by a process-wide lock. The app runs `gunicorn` with a
single worker, so one writer is always sufficient and there is no cross-process
contention.

> **Persistence summary:** user identities, roles, deployment records, and
> telemetry are all persistent. The only thing that is *not* persistent is the
> in-memory OAuth **session** — after a restart users sign in again, but their
> identity and admin role are restored from the database.

### Schema

Three tables. The full DDL is in `SCHEMA` in [`db.py`](db.py).

#### `users` — who has signed in, and their role

| Column | Type | Why it exists |
| --- | --- | --- |
| `oid` | TEXT PK | Entra **object ID** — the stable, immutable identifier for a user. For an admin who pre-assigns a role to someone who hasn't signed in yet, a placeholder row is keyed `upn:<upn>` and reconciled into a real `oid` row on first sign-in. |
| `upn` | TEXT | User principal name (email-style sign-in name). Human-readable; can change, so it is **not** the key. |
| `name` | TEXT | Display name, for the admin UI. |
| `role` | TEXT | `admin` or `guest`. **Admins** manage roles and see telemetry; **guests** can browse and deploy. The very first person to ever sign in (when no admin exists) is bootstrapped to `admin`. |
| `created_at` | REAL | Unix epoch seconds of first sign-in. |
| `last_seen` | REAL | Unix epoch seconds of most recent sign-in. |

#### `deployments` — every provisioning request and its lifecycle

| Column | Type | Why it exists |
| --- | --- | --- |
| `id` | TEXT PK | Deployment identifier (also used to name the Azure resource group). |
| `use_case` | TEXT | Catalog use case being deployed (e.g. `web-app`). |
| `environment` | TEXT | Parameter profile used (`dev` / `prod`). |
| `engine` | TEXT | Which IaC engine ran the deploy: `bicep` (default) or `terraform`. |
| `user_oid` / `user_upn` / `user_name` | TEXT | **Who** requested it, denormalized so history survives even if the user row changes. Indexed by `idx_deploy_user`. |
| `subscription_id` | TEXT | Target Azure subscription. |
| `resource_group` | TEXT | Resource group the executor created. |
| `status` | TEXT | Lifecycle: `requested → deploying → active → expiring → destroying → destroyed`, or `failed`. Indexed by `idx_deploy_status`. |
| `created_at` | REAL | When it was requested. |
| `end_at` | REAL | Scheduled teardown time; the scheduler tears down rows past this. Indexed by `idx_deploy_end`. |
| `destroyed_at` | REAL | When teardown completed (nullable). |
| `gh_run_url` | TEXT | Link to the portal/run for the deploy, surfaced in the UI. |
| `detail` | TEXT | Last status message / error, shown to the user. |
| `params_json` | TEXT | JSON blob of the parameters the user supplied. Stored as text and parsed on read (`_row_to_deployment`). |

#### `events` — append-only telemetry

| Column | Type | Why it exists |
| --- | --- | --- |
| `id` | INTEGER PK | Auto-increment row id. |
| `ts` | REAL | Unix epoch seconds. Indexed by `idx_events_ts`. |
| `type` | TEXT | Event kind: `view`, `signin`, `deploy_requested`, `deploy_succeeded`, `cleanup_triggered`, … Indexed by `idx_events_type`. |
| `user_oid` / `user_upn` | TEXT | Who triggered it (blank for anonymous). Indexed by `idx_events_user`. |
| `use_case` | TEXT | Related catalog use case, when applicable. |
| `deployment_id` | TEXT | Related deployment, when applicable. |
| `meta_json` | TEXT | Free-form JSON for extra context per event type. |

`events` is **append-only** — it is never updated in place, which keeps the
admin dashboard's aggregates (`telemetry_summary`) cheap and the audit trail
intact.

### Why SQLite (and not a managed DB)

- **Zero infrastructure / cost** — the gallery is an internal tool with modest
  write volume (sign-ins and deployments), so a single file is plenty.
- **One writer by design** — a single gunicorn worker is required anyway because
  OAuth sessions are in-memory, so there is no multi-writer problem to solve.
- **Persistent and portable** — it sits on the App Service `/home` share, backs up
  with the app, and can be copied off for inspection.

### Working with the data

All access goes through helpers in `db.py` — callers never write SQL directly:

- Users / roles: `upsert_user`, `get_role`, `list_users`, `set_user_role`, `has_admin`.
- Deployments: `create_deployment`, `update_deployment`, `get_deployment`,
  `list_deployments`, `due_deployments`.
- Telemetry: `record_event`, `telemetry_summary`.

To inspect the database locally:

```powershell
# Point the app at a local file, or open the live one with the SQLite CLI.
$env:GALLERY_DB_PATH = "$PWD\data\techlabs.db"
sqlite3 .\data\techlabs.db ".tables"
sqlite3 .\data\techlabs.db "SELECT upn, role, datetime(last_seen,'unixepoch') FROM users;"
```

## Deployment engines (Bicep and Terraform)

### External subscription deployments

The deploy modal keeps the existing managed-identity path as **Deploy to my
subscription** and offers **Deploy to external subscription** for a customer
or user-owned subscription. The external path requires the signed-in user to
already have the necessary Azure permissions on that subscription; entering a
subscription ID does not grant access. It uses the user's delegated ARM token,
so the gallery managed identity does not need access to that subscription.

External deployments currently support Bicep only. The delegated token remains
in process memory and is never written to SQLite. Manual cleanup must be started
while the user's gallery session is active; scheduled cleanup after a restart
cannot authenticate as the user and will fail closed.

Every catalog use case ships **both** a Bicep module (`catalog/<uc>/main.bicep`)
and a Terraform module (`catalog/<uc>/terraform/`). Each deployment records which
one ran in the `engine` column. The gallery card shows a "Bicep + Terraform" chip,
and the deploy modal lets the user pick the engine.

- **Bicep** is the default and needs no extra configuration. `tools/Build-Templates.ps1`
  precompiles each `main.bicep` to ARM JSON under `_templates/`, and `azure_deploy.py`
  submits it with the managed identity.
- **Terraform** is the second engine. The same script materializes each module under
  `_tf/<use_case>/` (the runtime tree has no `catalog/`), and `terraform_exec.py`
  runs `init`/`apply`/`destroy` with the managed identity.

### The deploy modal always shows the Terraform CLI

Regardless of host configuration, the deploy modal exposes the exact
`terraform init/apply` commands for the selected use case and environment, so a
user can run the module locally or in a pipeline.

### Enabling in-app Terraform deploys on App Service

The **in-app** "deploy with Terraform" option is capability-gated: `GET /api/me`
returns `terraformExecAvailable`, and the UI hides the option until the host can
resolve a `terraform` binary. Shipping with these unset is therefore safe (the
button simply never appears). To turn it on, set these **App Service application
settings**:

| Setting | Value | Purpose |
| --- | --- | --- |
| `GALLERY_TERRAFORM_AUTO_INSTALL` | `true` | Let the app fetch a pinned Terraform release into `/home/bin` on boot (or pre-stage a binary and set `GALLERY_TERRAFORM_BIN` instead). |
| `ARM_USE_MSI` | `true` | The `azurerm` provider authenticates as the app's managed identity. |
| `ARM_SUBSCRIPTION_ID` | `<sub guid>` | Target subscription (defaults to the deployment's subscription when omitted). |
| `ARM_TENANT_ID` | `<tenant guid>` | Tenant for the managed identity (recommended). |

The managed identity already deploys Bicep at subscription scope, so it can run
Terraform with the same rights — **no new role is required for local state**,
which lives on the persistent `/home` share (sufficient for the single gunicorn
worker). For an **azurerm remote backend**, also set `GALLERY_TF_STATE_STORAGE`
(plus `GALLERY_TF_STATE_RG` / `GALLERY_TF_STATE_CONTAINER`) and grant the identity
**Storage Blob Data Contributor** on that storage account.

```powershell
# Enable in-app Terraform deploys (local state) on the gallery web app.
az webapp config appsettings set `
  --name techlabs-gallery-w362xa --resource-group <rg> `
  --settings GALLERY_TERRAFORM_AUTO_INSTALL=true ARM_USE_MSI=true `
             ARM_SUBSCRIPTION_ID=<sub-guid> ARM_TENANT_ID=<tenant-guid>
```

> **Integrity note:** auto-install downloads the pinned Terraform zip over HTTPS
> from `releases.hashicorp.com` and verifies it against the release `SHA256SUMS`.
> For a stronger supply-chain posture, pre-stage a vetted binary on the `/home`
> share and point `GALLERY_TERRAFORM_BIN` at it (leave auto-install off).

## Running locally

```powershell
cd site/server
pip install -r requirements.txt
$env:GALLERY_ENTRA_TENANT_ID = "<your-tenant-guid>"   # see .env.example
uvicorn app:app --host 127.0.0.1 --port 8000
```

Then browse http://localhost:8000/ and sign in. See [`.env.example`](.env.example)
for all configuration knobs (tenant, client ID, subscription, deploy location,
HTTPS, cleanup interval, DB path).
