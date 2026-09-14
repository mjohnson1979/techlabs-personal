# Deployment Portal

Engineering preview of a customer-operated deployment catalog. Not approved for
external publication or production use. Review and approval are required before
external distribution.

## Setup sequence

The supported default is **customer Entra pre-work -> private container deployment
-> company sign-in -> administrator-only GitHub and subscription setup**. No
publisher registration, source-code edits or setup secret are required. The
code-protected installer is a separate opt-in alternative, not a default step.

1. Complete [prerequisites](#prerequisites) and provision [private hosting](#private-container-deployment).
2. Configure [Entra sign-in and the initial administrator](#entra-pre-work-and-first-sign-in).
3. [Build and deploy the image](#deploy-and-start-the-image) using your approved registry and package feed.
4. Complete [GitHub](#2-github) and [subscription/pricing](#3-subscriptions-and-pricing) setup in the browser.
5. [Publish reviewed workloads](#publish-your-own-workloads) and run the [acceptance checks](#acceptance-checklist).
6. Establish [operations and recovery](#updates-and-recovery); use [troubleshooting](#troubleshooting) for failures.

## Prerequisites

| Area | Customer prerequisite |
| --- | --- |
| Source and tools | Approved history-free customer source, Git, Docker with a running Linux engine/BuildKit, base-image access and an approved Python package feed. No local Python installation is required for Docker builds. |
| Azure | Azure public-cloud subscription/workforce tenant; permission to configure Linux App Service, private networking and registry. Select an App Service plan supporting custom containers and private endpoints with capacity/costs approved by the company. |
| Role assignments | A separately authorized operator can assign reviewed Azure roles. Contributor alone cannot grant role assignments. |
| Entra | Permission to create/manage a single-tenant Web registration and its credentials, plus any consent required by company policy. No Graph application-creation permissions are needed by the default app runtime. |
| Private connectivity | Private endpoint, company/VPN routing and private DNS. Review SCM/deployment access and outbound routing to private registries/Key Vault independently. |
| Registry | A company-owned image repository; build identity with push permissions and host managed identity with pull permissions. For ACR use AcrPull for RBAC-only registries or Container Registry Repository Reader for ABAC-enabled registries. |
| Runtime state | One worker, one instance, persistent `/home/data` writable by UID/GID 10001. The stock image contains Python 3.12, but no Terraform CLI. |
| Portal ownership | Initial administrator's user object ID in the hosting tenant, configured through GALLERY_ADMIN_OIDS. Tenant membership, app ownership and Azure roles do not automatically grant portal roles. |
| Optional integrations | Customer GitHub OAuth App with device flow; existing Key Vault with applicable read/write and network access; supported billing agreement/permissions for negotiated pricing. |

The host managed identity needs Reader on the hosting subscription for startup
discovery and on each target subscription for metadata lookup. Grant Website
Contributor on this app only (or narrower equivalent) for ongoing token-storage
and provider-configuration writes. Deployment and cleanup permissions are
separate, workload-specific grants in each approved subscription. The browser
user's Azure permissions are not delegated to the host identity. Neither setup
nor adding a subscription grants these roles automatically.

## Ownership and license

The platform source is provided under the included MIT License, including its
"AS IS" warranty disclaimer and limitation of liability for authors and copyright
holders. Retain the copyright and permission notices in copies or substantial
portions. The license does not grant trademark rights or imply endorsement.
No support service or service-level agreement is included with this distribution.
Separate support contracts and cloud service terms, if any, remain applicable.
Dependencies retain their respective licenses; the MIT License does not relicense
third-party components. Review dependency notices before redistribution.

You may name and brand your installation using `GALLERY_BRAND_NAME`. Microsoft
product names appear only to identify dependencies and services, not as the
platform's brand. No logos or service icon bundle are included.

## What is included

- Platform application source and an empty catalog.
- A blank configuration example and a SHA-256 file manifest.
- No infrastructure templates: no Bicep, Terraform, compiled ARM templates,
  parameter files, Terraform state, or bundled sample workloads.
- No source repository history, workflows, internal documentation, snapshots,
  application databases, logs, submissions, credentials, or deployment artifacts.

Use a new repository for this archive. Do not publish a fork, branch, mirror, or
archive of the originating internal repository: its history and other paths are
not part of this distribution. The source allowlist and known-content checks are
engineering safeguards, not proof that every confidential item has been identified.

## Customer hosting

### Private container deployment

Clone the approved customer source repository into your company's private
repository, build the image, and push it to your own registry. Do not clone or
publish the originating development history. No source-code changes, bundled
workloads or publisher app IDs are required. **Customer-owned Entra sign-in is
configured as deployment pre-work**, before anyone opens the site.

From the customer repository root, authenticate to your registry through the company's
approved credential-helper/login process before pushing. Replace the example
registry and release with your own values:

```powershell
$image = 'registry.example.com/deployment-portal:release-1'
docker build --platform linux/amd64 --tag $image .
if ($LASTEXITCODE -ne 0) { throw 'Container build failed' }
docker push $image
if ($LASTEXITCODE -ne 0) { throw 'Image push failed' }
```

If company policy requires an approved Python package feed, pass your existing
pip configuration as a **build secret**, not as a Docker build argument or source
file. Docker does not automatically inherit the host machine's pip configuration:

```powershell
docker build --platform linux/amd64 --secret "id=pip_config,src=C:\path\to\approved\pip.ini" --tag $image .
```

On Linux, use the path to your approved `pip.conf` instead. The configuration is
mounted only during dependency download and is not copied into the final image.
Without this optional secret, pip uses its normal public index. Keep TLS
verification enabled; do not use `trusted-host` or disable certificate checks to
work around corporate network policy. If the feed changes, invalidate the
dependency-stage cache with `--no-cache-filter dependencies`.

The multi-stage build copies only the source allowlist and empty snapshots.
It does not copy local databases, Git history, tests, secrets or development
instructions. The runtime uses Python 3.12 and UID/GID 10001, with one worker on
port 8000. Its embedded source seed allows a customer-owned repository to be
created from the browser without inheriting another repository's history.
Pin the deployed image by its registry digest after your security/license scan;
maintain your approved Python base-image digest and dependency versions for
repeatable production releases. The supplied Python base tag receives updates.

Configure the customer-owned **App Service for Linux custom-container host**:

| Hosting control | Required configuration |
| --- | --- |
| Network | Disable public network access; provide a private endpoint, private DNS and routing from company network/VPN. Protect SCM separately. |
| HTTPS | HTTPS-only on the site and HTTPS required in App Service Authentication. |
| Authentication | Easy Auth enabled with your single-tenant Microsoft Entra provider; require authentication for all requests, no excluded paths, and redirect unauthenticated browsers to that provider. Never expose the container directly. |
| Identity | Enable the system-assigned identity. Grant Reader on the hosting subscription for tenant verification and Website Contributor (or narrower equivalent) on this app for setup writes. |
| Registry | Customer-owned image; grant the host identity the appropriate image-pull role. Private registries require working outbound routing/DNS. |
| Storage | Set `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`; `/home/data` must be writable by UID 10001 and persist across replacement containers. Start with a fresh database. |
| Process | One instance; container target port 8000 (`WEBSITES_PORT=8000` for classic custom containers). Keep the image entrypoint, without a custom startup override. |
| Initial administrator | Set `GALLERY_ADMIN_OIDS` to the intended administrator's Entra object ID in the hosting tenant. This is a non-secret identifier, not an email or application client ID. |

Hosting controls and Azure permissions belong to your platform deployment;
they are not granted or relaxed by the application. A secured tenant alone
does not make an App Service endpoint private. The container verifies disabled
public access, site identity, HTTPS, Easy Auth and its attached managed identity
before listening. It does not create the private endpoint or verify your VPN's
membership policy. Restrict setup access to deployment administrators.

The container derives its host resource ID and HTTPS origin from App Service's
read-only `WEBSITE_*` metadata, then reads the existing Entra provider through
the Azure management API. It verifies the client audience and tenant-specific
v2 issuer against the tenant that owns the hosting subscription. System-assigned
identity needs no client-ID setting. Explicit `GALLERY_ENTRA_TENANT_ID` or
`GALLERY_ENTRA_CLIENT_ID` overrides are accepted only if they match the provider.
Discovery performs no Azure writes and does not retrieve the provider's secret.

**No setup secret, digest or expiry setting is required for this default flow.**
Leave `GALLERY_INSTALLER_ENABLED` unset or `false` and leave all `GALLERY_SETUP_*`
settings unset. A leftover setup secret alone never enables the code installer.
Missing sign-in configuration or administrator IDs blocks startup rather than
opening anonymous setup. Authentication settings and the database are not reset
when the image is replaced.

### Entra pre-work and first sign-in

1. In the tenant owning the hosting subscription, create or select a dedicated
   single-tenant **Web** app registration. Alternatively use **App Service >
   Authentication > Add identity provider > Microsoft > Create new app registration**.
   Configure the exact redirect URI
   `https://<your-site>/.auth/login/aad/callback` and the ID-token issuance required
   for Easy Auth's hybrid flow. Keep the registration customer-owned.
2. In App Service Authentication, configure that registration with the issuer
   `https://login.microsoftonline.com/<tenant-id>/v2.0`. Require authentication on
   every path and HTTP 302 login redirection to Microsoft. Do not add other
   audiences: the app client ID is implicitly accepted, or may be listed explicitly.
   Store the provider credential in protected App Service settings or Key Vault;
   rotate it before expiry. No Microsoft Graph app-creation permissions are
   required by this default setup flow.
3. Set `GALLERY_ADMIN_OIDS` to the intended initial administrator's **user object
   ID in this tenant**. Multiple candidate IDs may be comma-separated; the first
   eligible sign-in becomes administrator when no administrator exists. Additional
   administrators are assigned through the portal's admin controls. Updating the
   candidate list does not revoke or synchronize existing database roles. This is
   separate from Entra directory roles and Azure resource roles: being a tenant
   member, application owner or subscription Owner does not automatically make
   someone a portal administrator.
4. Deploy/start the image after the private networking, Easy Auth, managed identity
   and storage controls in the hosting table are configured. No application
   source edits or separate copies of the Entra tenant/client IDs are needed.
5. Open the site's root URL from the company network/VPN. Easy Auth redirects to
   company Entra sign-in and enforces the organization's sign-in policies. Use
   the root URL, not the OAuth callback as a bookmark. The callback only processes
   a valid sign-in response; opening it directly is not a sign-in test.
6. After the designated administrator signs in, **Setup** opens automatically
   when no deployment subscriptions are configured, starting at **GitHub**.
   Configure the company OAuth application and repository, then **Subscriptions**.
   **Authentication** shows the existing primary connection and optional additional
   providers; creating a second provider is not required. Other tenant users
   remain guests and cannot open or modify administrator setup.

Never use a publisher registration or the Azure CLI client ID for this application.
The local read-only preview does not simulate real Entra authentication or consent.

Reference: [Configure App Service Entra sign-in](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad).

Optional advanced overrides: `GALLERY_PUBLIC_URL` for a custom HTTPS hostname
already bound to the app; `GALLERY_HOST_RESOURCE_ID` if platform metadata is
unavailable; `AZURE_CLIENT_ID` to select an attached user-assigned identity
instead of the system identity. Do not change identities midway through setup.
The raw source/ZIP deployment described below remains an advanced alternative
and does not run the container's automatic configuration.

References: [App Service environment metadata](https://learn.microsoft.com/azure/app-service/reference-app-settings),
[custom-container configuration](https://learn.microsoft.com/azure/app-service/configure-custom-container),
and [private endpoints](https://learn.microsoft.com/azure/app-service/overview-private-endpoint).

### Deploy and start the image

1. Build and push with the commands above, scan the image/dependencies and record
   its registry digest. The stock base tag and minimum-version dependencies may
   change; maintain your approved versions for repeatable releases.
2. In **App Service > Deployment Center**, or your approved deployment tooling,
   select the registry, image repository and release. Prefer an immutable digest
   where supported; otherwise use a non-reused tag and record the resolved digest.
3. Configure managed-identity image pulls and the applicable registry role.
   Private image pulls also need correct outbound routing and DNS; the site's
   inbound private endpoint alone does not supply those paths.
4. Verify the three required host settings: `GALLERY_ADMIN_OIDS`,
   `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`, and `WEBSITES_PORT=8000` for classic
   containers. With sidecar-enabled hosting, configure the main container's target
   port as 8000 instead. Keep the image entrypoint and one instance/worker.
5. Confirm that Entra, HTTPS and private connectivity are configured before
   starting the app. Leave all `GALLERY_SETUP_*` settings unset and the installer
   disabled. For the default system identity, omit `AZURE_CLIENT_ID`; host and
   Entra IDs are discovered rather than copied manually.
6. Save/start/restart and check deployment status and container logs. An accepted
   image deployment does not establish startup success. Allow image-pull/startup
   time and investigate the recorded failure before submitting another deployment.
7. Open the root URL from the company network/VPN and complete Entra sign-in as
   the designated administrator. Use the account menu's Setup entry if needed.

For mounted storage, confirm UID 10001 can write `/home/data`; mount permissions
may differ from the image's directory permissions. Do not solve a storage error
by running the app as root. The configuration example is not automatically
loaded, and copying its blank values into host settings can override defaults.
The Entra authentication credential is a secret managed by the hosting platform;
the administrator object ID is not a secret.

### Local read-only preview

Extract the customer archive into a new directory. With Python 3.12 installed,
run this from the extracted root (no packages or cloud credentials are needed):

```powershell
python tools/preview_customer.py
```

Open `http://127.0.0.1:8766/` in your normal browser. Use `--port 8767` if the port
is occupied. The launcher binds only to loopback and serves the exported UI with
a synthetic administrator and empty deployment records. It does not import the
production backend, create a database, authenticate a real user or contact Azure.
Writes, uploads, role changes and deployments return a read-only error. The empty
catalog contains no sample infrastructure. Stop with Ctrl+C. Never expose or
tunnel this preview server; it is not an authentication or deployment test.

When running the launcher from the development repository instead, pass
`--root <path-to-extracted-customer-archive>`. The launcher requires the customer
distribution marker and cannot preview the internal source tree by default.

### Configuration preflight

Set your customer environment settings, then run from the extracted root:

```powershell
python site/server/distribution.py --check
python site/server/distribution.py --check --json
```

Both commands check the same rules enforced at application startup, using only
the Python standard library. They report all invalid/missing settings, print no
configured values, perform no network calls and write no files. Exit code `0`
means local settings passed; `1` means configuration problems; `2` means invalid
command arguments. `--json` provides structured output for customer CI.

This standalone check does not perform the container's Azure discovery. When
using it outside the container, supply the explicit tenant/client IDs as well as
the other required configuration; the deployed container discovers those IDs
before applying the same validation. Missing IDs in an unconfigured local shell
are therefore expected, not a reason to disable validation.

The example environment file is not automatically loaded. A passing result does
not verify resource ownership, tenant membership, Easy Auth ingress, RBAC or
release approvals. Use your actual customer configuration for deployment checks,
not the synthetic identity used by the preview.

### Optional code-protected installation

This alternative is **not the default**. Use it only when your deployment design
deliberately postpones primary Entra configuration until browser installation.
It supports the workforce tenant owning the hosting subscription in Azure public
cloud. Existing providers and databases with users cannot be claimed or replaced.

For a container, explicitly set `GALLERY_INSTALLER_ENABLED=true` and supply one
unique random `GALLERY_SETUP_SECRET` of 32-128 characters through protected host
settings or a resolved Key Vault reference. Enable Easy Auth and HTTPS but allow
unauthenticated ingress through that platform to the code-protected installer;
do not configure a provider first. The application locks normal APIs/jobs until
the code owner verifies Entra sign-in in the same browser. The container persists
a one-hour installation window and a single-use session lasting at most 30 minutes.
Restarts do not extend expiry. Lost/expired sessions require a new host secret;
previously used secrets cannot renew the window. Completion removes the secret
and disables installation. Retain the persistent database during recovery.

Continue with the numbered browser steps below. The following manual settings
apply only to raw source/ZIP hosting without the container entrypoint.

Provision a customer-owned Linux App Service, persistent fresh database and
user-assigned managed identity. Use Python 3.12, one instance and one worker:

```text
gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000
```

Deploy the contents of `site/` to the app root and install `requirements.txt`.
The host must be HTTPS-only. Enable App Service Authentication, initially with
unauthenticated access allowed and no configured identity providers. **Do not
disable the authentication platform**: it strips caller-supplied identity headers.
Do not expose the backend through a route that bypasses that platform. During
installation, the application blocks normal APIs, assets, deployment operations
and background jobs. Only the installer and health endpoint are available.

Set these protected host settings before opening the site:

| Setting | Value |
| --- | --- |
| `GALLERY_INSTALLER_ENABLED` | `true` |
| `GALLERY_EASY_AUTH`, `GALLERY_HTTPS` | `true` |
| `AZURE_CLIENT_ID` | Attached user-assigned identity's client ID |
| `GALLERY_HOST_RESOURCE_ID` | This App Service's full resource ID |
| `GALLERY_PUBLIC_URL` | Exact HTTPS origin, without trailing slash |
| `GALLERY_SETUP_CODE_SHA256` | SHA-256 digest of a unique random setup code |
| `GALLERY_SETUP_EXPIRES_AT` | Timezone-aware UTC expiry, at most 24 hours away |
| `GALLERY_DB_PATH` | Fresh persistent database, e.g. `/home/data/deployment-portal.db` |

Leave `GALLERY_ENTRA_TENANT_ID`, `GALLERY_ENTRA_CLIENT_ID`, `GALLERY_ADMIN_OIDS`,
`GALLERY_SUBSCRIPTION_ID` and `ARM_TENANT_ID` empty. The installer writes the
Entra IDs and verified initial administrator after successful sign-in. Grant
the managed identity Reader on the hosting subscription for tenant verification
and Website Contributor (or a narrower custom role) on **this app only** for
configuration. No Graph permissions or workload deployment roles are needed to
connect an existing registration. These permissions are not granted by setup.

Generate the code in a private local PowerShell session, not a shared log or chat:

```powershell
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $bytes = [byte[]]::new(32)
    $random.GetBytes($bytes)
    $setupCode = [Convert]::ToBase64String($bytes)
    $digest = [BitConverter]::ToString($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($setupCode))).Replace('-', '').ToLowerInvariant()
    [pscustomobject]@{
        SetupCode = $setupCode
        GALLERY_SETUP_CODE_SHA256 = $digest
        GALLERY_SETUP_EXPIRES_AT = [DateTimeOffset]::UtcNow.AddHours(1).ToString('o')
    }
} finally {
    $random.Dispose()
    $sha256.Dispose()
}
```

Enter the digest and expiry in protected host settings; keep the code private
and enter it only in the browser installer. Never put it in a URL. The code is
consumed on unlock. Its secure, HttpOnly browser session lasts up to 30 minutes,
survives worker restarts, and never outlives the installation expiry. Lost or
expired sessions require a new code digest and expiry in host settings; rotating
the digest invalidates the old session. Requests are rate-limited and same-origin.

1. Open the site, or `/install`, and enter your setup code.
2. Select or create a single-tenant **Web** app registration in your Entra tenant.
   The browser displays its required redirect URI,
   `https://<your-host>/.auth/login/aad/callback`. Configure ID-token issuance for
   Easy Auth's hybrid `code id_token` flow. The installer does not create a
   registration, grant consent or alter your Entra directory for you.
3. Enter the tenant ID, application client ID and client secret. Select App
   Service settings or your existing Key Vault. The secret is transmitted over
   HTTPS to the host and stored server-side, never returned by the API or saved
   in the application database. The fixed host setting is
   `PORTAL_ENTRA_CLIENT_SECRET`. **Use stored credential** reuses that setting or
   an existing Key Vault secret without entering the value. Existing values are
   not silently overwritten. Key Vault prerequisites below apply.
4. Select **Connect Entra**. Host-setting changes can restart the app; reload
   in the same browser to resume. If a credential was stored before a request
   was interrupted, select **Use stored credential** when reconnecting.
5. Select **Sign in with Entra** in that same browser. Complete your organization's
   sign-in and consent requirements, then select **Verify and finish**. Both
   the installation session and matching Entra tenant, audience, issuer and
   object ID are required. That verified user becomes the initial administrator.
6. The installer enforces authentication on all paths, removes the setup-code
   configuration and disables installation. After the host restarts, open the
   portal and complete GitHub and Subscriptions yourself. No subscription quota
   checks occur during initial setup.

An existing provider or database with users cannot be claimed by the installer.
Completion is persisted, so leaving an old installer flag or code behind cannot
reopen a completed installation. Partial activation stays locked and can only
finish as the already-verified owner. Keep the database and host settings in
sync during recovery. For interrupted activation with expired credentials, use
the Azure hosting portal as the owner recovery path; do not delete the database
or relax normal authentication to bypass it.

References: [App Service Entra configuration](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad)
and [Easy Auth flows](https://learn.microsoft.com/azure/app-service/overview-authentication-authorization#client-type-and-oauth-flow-behavior).

### Raw source hosting with preconfigured authentication

This version supports one customer Azure tenant and multiple deployment subscriptions,
hosted on customer-owned Azure App Service for Linux with App Service
Authentication (Easy Auth). Self-hosted OAuth, external-subscription authorization,
automatic guest invitations, and Terraform auto-download are unavailable.

1. Provision your own App Service, persistent storage, single-tenant Entra web app
   registration, and user-assigned managed identity. No hosting IaC is supplied.
2. Configure App Service Authentication to require authentication for **all**
   requests. Use your tenant-specific v2 issuer and only your app's client ID as
   the allowed audience. Configure the web redirect URI as
   `https://<your-host>/.auth/login/aad/callback`. Disable token storage unless
   needed for another customer-approved purpose. Configure authentication
   credentials in the hosting platform, never in source code.
3. Enable HTTPS-only access. Ensure requests cannot reach the backend while
   bypassing Easy Auth or preserving caller-supplied `X-MS-CLIENT-*` headers.
   Setting the application flag alone does **not** secure the ingress.
4. Attach your user-assigned managed identity and grant only the Azure roles
   required by your reviewed workloads, scoped to your deployment subscription.
   Do not grant access to unrelated subscriptions. Creating resource groups
   requires subscription-level permissions. Templates assigning roles require
   separately reviewed, constrained role-assignment permissions.
5. Set the values in `site/server/.env.example` as environment/app settings.
   The application does not load that file automatically. Tenant, app, identity,
   and administrator IDs must be nonzero GUIDs. The optional
   `GALLERY_SUBSCRIPTION_ID` can be blank: an administrator adds subscriptions in
   the hosted Setup panel. If supplied, it must be a nonzero GUID. Set
   `GALLERY_ADMIN_OIDS` to a comma-separated list of your intended bootstrap
   administrators' Entra object IDs; the first eligible one to sign in becomes
   administrator. An arbitrary first visitor does not become administrator.
6. Deploy the contents of `site/` to the app root. Install from its
   `requirements.txt` and use Python 3.12 with this startup command:

   ```text
   gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000
   ```

Use a fresh, customer-controlled database for every installation. Do not import
an internal deployment database or reuse it between tenants. The current storage
and scheduler design requires **one worker and one app instance**. Scale-out and
multi-tenant hosting are not supported. Confirm sign-in with your tenant, denial
of other tenants, administrator assignment, deployment, and cleanup in your own
non-production subscription before use.

The `site/distribution.json` marker permanently selects the customer restrictions
for this package, even when the environment flag is omitted or set to `internal`.
Startup rejects missing or inconsistent customer configuration.

## Hosted initial setup

The **Setup** entry in the administrator account menu opens Authentication,
GitHub, and Subscriptions. It opens automatically when an administrator signs in
and there are no deployment subscriptions. Every setup endpoint requires an
authenticated administrator; writes require a same-origin browser request.

This panel follows the default preconfigured Entra sign-in, or the optional
protected browser installation. It shows the primary Entra connection; additional
providers are optional. In the default flow, only a designated initial
administrator can bootstrap the admin role after Entra sign-in; arriving first
as an ordinary tenant member does not qualify. The installation code is required
only for the explicitly enabled alternative installer. Keep the primary provider
until any additional provider has been verified.

### 1. Authentication

The primary provider is already configured for the default sign-in-first flow.
Review that connection; the following actions under **Additional identity provider
(optional)** are not required to complete onboarding.

- Choose Entra ID or another OIDC provider. Set the provider name, app origin,
   client ID, issuer and discovery document. **Client secret storage** explicitly
   selects **App Service settings**, **Azure Key Vault**, or **No client secret**.
   The no-secret option is available only for an existing OIDC provider that
   permits authentication without a client credential, not for Entra registration
   creation. No-secret mode omits the credential configuration and does not access
   secret settings. Do not paste secret values into the wizard.
- For Entra, **Create Entra registration** creates a single-tenant application
   and service principal. A 180-day credential is stored in the selected destination:
   an encrypted App Service setting, or a new secret in the chosen Key Vault with
   only a reference in App Service. Only non-secret metadata and expiry are returned.
   The host may restart after settings change; reopen Setup to resume. The operator
   must rotate the credential before expiry. This is not automatic rotation.
- Registration automation requires customer-admin-consented Microsoft Graph
   `Application.ReadWrite.OwnedBy` for the managed identity, plus access to update
   this App Service's app settings. These privileges are **not** granted by setup.
   Prefer a separately reviewed setup identity/permission window and remove setup
   privileges after onboarding where practical.
- Set `GALLERY_HOST_RESOURCE_ID` to this exact App Service resource ID. Adding a
   provider requires Website Contributor or narrower equivalent permissions on
   that host. The public URL must match one of the host's registered hostnames.
- **Add provider to App Service** adds a custom OIDC provider and preserves the
   existing provider and default redirect. App Service continues to require HTTPS
   and authentication. Existing providers cannot be overwritten by this wizard.
- Register other OIDC clients in the provider's own console using the displayed
   callback URL. OIDC vendor registration is provider-specific, not automatically
   provisioned. For App Service storage, configure the existing secret setting
   before adding the provider. For Key Vault storage, provide the vault resource
   ID, existing secret name and an unused target app-setting name; setup creates
   the reference without exposing the secret. App Service performs OIDC discovery
   and token validation.
- Use **Test sign-in** in a new browser session. Runtime accepts only the configured
   provider, issuer, audience and stable subject claims. Missing claims fail closed.
   The returned status means "configured; test required", not verified sign-in.
   Some providers require claim mapping adjustments in their console.
- OIDC users receive a namespaced subject identity and start as guests. After
   they sign in, the bootstrap administrator can change their role from the Users
   table by exact identity. Email-based invitations do not grant OIDC admin access.
   OIDC subjects cannot receive automatic Azure Reader grants.
- After testing, change the default sign-in provider using the hosting portal if
   desired. The wizard deliberately does not remove or replace the bootstrap path.

Key Vault storage uses an **existing vault in the customer Azure tenant**, not a
new vault provisioned by the wizard. The vault's ARM identity and public-cloud
endpoint are verified. Configure the host's `keyVaultReferenceIdentity` to the
attached user-assigned identity identified by `AZURE_CLIENT_ID`, or leave it at
the system-assigned default when using the container's system identity. Setup does not
change this app-wide property because doing so could break other references.
Grant that identity Reader access to the vault resource and secret Get/Set access
for generated credentials, or Get access for existing credentials. Equivalent
RBAC roles are Key Vault Reader plus Key Vault Secrets Officer (generation), or
Key Vault Reader plus Key Vault Secrets User (read only); narrower custom roles
are also possible. Vault networking must permit access from the application.
No permissions or network rules are granted automatically.

New references pin a secret version. The operator must update them when rotating
credentials; pre-existing matching versionless references are accepted. A failed
vault write does not fall back to storing the credential value in App Service.
Generated secrets cannot overwrite an existing vault secret name, and setup does
not silently migrate an already-created credential to another destination. A host
settings change can restart the app; reopen setup after references have resolved.
App Service resolves the reference into runtime memory, so Key Vault storage does
not mean the authentication service never reads the secret.

Azure deployment, subscription and pricing access use managed identity and do not
require a stored Azure client secret. Their identity IDs are not secrets.

Reference: https://learn.microsoft.com/azure/app-service/app-service-key-vault-references

### 2. GitHub

- After saving your customer OAuth application as described below, enable GitHub
   and select **Connect GitHub**. The app displays a short verification
   code and a link to `https://github.com/login/device`. Enter the code there and
   authorize the customer OAuth App. The wizard polls at GitHub's permitted interval,
   handles pending/slow-down/denied responses, and expires abandoned sign-ins.
   Device codes and access tokens remain server-side; the browser receives only
   the user code, status and discovered account/repository information.
- Register a **customer-owned GitHub OAuth App**, enable its Device Flow, and
   enter its public client ID under **Setup > GitHub > GitHub OAuth application**.
   No host-setting edit is needed. Legacy `GALLERY_GITHUB_OAUTH_CLIENT_ID` settings
   remain supported when no application has been saved in Setup. An active
   connection cannot silently switch to a different OAuth application.
   Device flow needs no OAuth client secret. No publisher/Scout application ID is
   shipped or reused. The OAuth App consent requests `repo` and `read:org` for
   repository operations and account discovery. These are user-level OAuth scopes,
   not GitHub App installation permissions; review their breadth before approval.
- After authorization, select from the repositories and accounts returned by
   GitHub. The existing repository's default branch is selected automatically.
   Alternatively choose an account and enter only a new repository name to create
   a **private** repository and copy the platform source. Creation/copying require
   explicit confirmation. No upstream history, internal repository, IaC samples
   or runtime data are copied. Creation may be restricted by organization policy
   or SSO even when an organization appears in the account list.
- No token, owner name or branch needs to be typed in the normal flow. Discovery
   lists writable, non-archived repositories, with up to 500 repositories and
   organizations queried; truncation is stated rather than silently hidden.
- **Token storage (advanced)** retains the App Service/Key Vault choice. The default
   stores the authorized token in encrypted App Service settings; an existing
   configured Key Vault destination is respected. A new vault destination needs
   a dedicated secret name, and all vault prerequisites above apply. Device sign-in
   updates only its own tagged Key Vault secret, never an unrelated secret. The
   token is also used in process memory after successful persistence. No plaintext
   token is stored in the application database or returned to the browser.
- Persisting the connection requires host app-settings write access and can restart
   the host. Reopen Setup if that interrupts polling; no callback URL or manual
   token copy is required. Failures keep pending authorization in memory only for
   the device-code lifetime so it can retry without exchanging the same code again.
- A token expiry supplied by GitHub is respected by intake and sync. This version
   does not retain refresh tokens or automatically refresh OAuth access tokens;
   reconnect after expiry/revocation. Long-lived tokens can also be revoked by the
   user or organization. Disabling a repository stops its sync/publishing but does
   not revoke or erase the stored credential. Revoke consent in GitHub when access
   is no longer needed. Disable the active repository connection before reconnecting
   another account, preventing credential replacement during active catalog sync.
- Preconfigured host tokens remain supported for existing installations, but the
   normal onboarding screen uses device sign-in. GitHub being disabled requires
   no credential. Secrets for Entra/OIDC authentication are separate and unchanged.
- Repository seeding is available only from a built customer export, which embeds
   an allowlisted, hashed source bundle. Seeding an existing repository is blocked.
   If a remote operation partially succeeds, inspect the private repository before
   retrying. The application does not delete partially created repositories.
- Uploaded use cases create review branches in this configured repository.
   Merge reviewed changes into the configured catalog branch. Protect that branch:
   it supplies executable infrastructure inputs to the managed-identity executor.
- Automatic sync runs every five minutes; **Sync catalog now** runs immediately.
   A sync pins all reads to one commit. On failure the previous catalog stays active.
   No webhook endpoint, workflow, CI credential or automatic app-code deployment
   is installed. App source changes still require the customer's deployment process.
- Disabling GitHub stops sync and publishing; the last synchronized catalog remains
   available. Immutable runtime versions are retained for deployment consistency
   and must be included in customer retention/backup procedures.

Reference: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow

Repository input layout (all workload code is customer-authored):

```text
catalog/<slug>/metadata.json
catalog/<slug>/compiled/dev.json
catalog/<slug>/compiled/prod.json
catalog/<slug>/terraform/main.tf
catalog/<slug>/terraform/<other module files>
```

Metadata appears in the app even without runtime code, but deployment is disabled
until a dev compiled ARM input or Terraform main module exists. Compile Bicep in
the customer's toolchain; sync does not execute Bicep builds or arbitrary scripts.
The compiled file format is the same `template`/`parameters` object described
below. Slugs are lowercase letters/digits/hyphens, up to 80 characters. Nested
Terraform directories are not currently synchronized. Source symlinks and
submodules are not imported. Sync is bounded to 500 files, 150 directories,
2 MiB per file and 16 MiB total. Those limits are intentional.

### 3. Subscriptions and pricing

Enter a subscription ID, or import a UTF-8 CSV with a header row:

```text
subscriptionId
```

The only required column is `subscriptionId`. An optional `pricingMode` column
accepts `retail` (the default) or `negotiated`. Do not supply names, tenant IDs or
regions: Azure returns each subscription's name and tenant using the application's
managed identity. The browser user's sign-in alone does not grant that identity
Azure access. Names and tenant membership are resolved for both individual and
CSV additions and rechecked before saving; browser-supplied metadata is rejected.

Quoted fields and UTF-8 BOMs are supported. Imports are limited to 128 KiB and
200 subscriptions. Review the resolved rows before **Verify and save subscriptions**.
Every subscription must be enabled, accessible to the managed identity, and owned
by the configured Azure tenant. A failed lookup does not save a partial list.
Saving verifies read access and tenant ownership, not every workload's deployment
permissions. Existing subscriptions cannot be removed in this version, to avoid
breaking cleanup of tracked deployments.

Deployment and estimate requests select from this persisted allowlist, which both
ARM and Terraform executors enforce. Adding a subscription does not grant RBAC.
Grant required deployment roles in each customer subscription separately.

After setup, **Check subscription quota** in the workload deployment view queries
subscription-specific Compute usage/limits for the selected region. Initial setup
does not run quota checks or present quota controls. A subscription has no single
region. Azure's subscription locations list is loaded when choosing a deployment
region; no region
is collected or stored as subscription metadata. These location names are not a
guarantee that a particular service or SKU is available. Prices use the selected
workload region, or the workload's authored cost region when no override is sent.
Other service quotas and physical regional allocation remain **unknown**;
quota headroom is not a capacity guarantee. Failures are explicitly reported as
unknown, never as unlimited availability.

**Negotiated prices** attempts the subscription-scoped Consumption Price Sheet API
using the managed identity. This requires billing-reader access and a supported
billing agreement. It does not claim to support all MCA/CSP/EA billing scopes.
Workload cost components must declare exact `meterId` values for their authored
region or `meterIdsByRegion` mappings for other regions. A single currency and
unit match is required; ambiguous, tiered/included-quantity, missing or overridden
SKU matches fall back to public retail estimates. Savings-plan/reservation/credit
and tax adjustments are not modeled. The pricing display identifies the selected
subscription, actual source, and fallback reason. Failed live retail lookup shows
the authored estimate marked potentially stale; it is never labeled negotiated.

References:
- https://learn.microsoft.com/azure/app-service/configure-authentication-provider-openid-connect
- https://learn.microsoft.com/graph/api/application-post-applications
- https://learn.microsoft.com/graph/api/application-addpassword
- https://learn.microsoft.com/rest/api/consumption/pricesheet/get

## Publish your own workloads

Author and review your infrastructure code in a customer-owned repository. The
runtime deployment contract is:

- `site/catalog.json`: an object with an `items` array. Each item needs a unique
  `path` and `name` (a lowercase slug), `displayName`, `category`, `description`,
  `engines` (Bicep and/or Terraform), and `hasProd`. Optional fields include
  `parameters`, `industries`, `compliance`, `architecture`, `quota`, and `cost`.
  Use `template` for the path to your own source when showing manual commands.
- Bicep: compile **your own** source and parameter files using your toolchain.
  Place a JSON object containing `template` (the compiled ARM object) and
  `parameters` (the parameter-name to value-object map) at
  `site/server/_templates/<path>/dev.json`, and optionally `prod.json`.
- Terraform: install and maintain the CLI yourself and place **your own** module
  at `site/server/_tf/<path>/`. The executor provides `resource_group_name`,
  `location`, `environment`, and `tags` variables. Parameter overrides use
  snake_case variable names. Optional environment files are `dev.tfvars` and
  `prod.tfvars`. Do not supply a backend block; the executor creates it.
- Optional customer-generated region, SKU, capacity, and lifecycle metadata may
  replace the empty snapshots. No background feed is connected by this package.

Redeploy your customer-built app artifact with those inputs. These are customer
additions, not templates supplied by this distribution. Uploads in the UI are
staged for administrator review, **not** automatically installed or executed.
GitHub publishing is off unless both a token and a customer-owned `owner/repo`
are explicitly configured. Do not provide a publisher-owned repository or token.

Only publish trusted code. Terraform providers, provisioners, and uploaded
deployment artifacts are executable infrastructure code with the host identity's
permissions; this is not a sandbox for untrusted workloads.

## Data and network boundaries

Identity claims, roles, deployment records, audit events and staged uploads are
stored on customer-controlled storage. Deployment metadata includes user object
IDs, names and UPNs, so the installation does process personal data. Optional UI
usage-event collection is disabled in customer mode. The operator must configure
retention, deletion, backup, access control and incident procedures.

There is no publisher-operated telemetry endpoint, remote support channel, or
publisher credential in the package. Do not configure publisher-controlled
monitoring, repositories or identities. Restrict operational access to your staff.

Necessary service traffic still exists:

| Destination | Purpose |
| --- | --- |
| Customer Entra tenant and App Service Authentication | Sign-in and session handling |
| Azure management APIs | Customer-authorized resource creation and deletion |
| Microsoft Graph, during confirmed Entra setup or optional Reader grants | Create customer app registration/credentials or resolve principals; guest invitations remain disabled |
| Customer OIDC provider, when configured | Customer login and claims through App Service Authentication |
| Azure Consumption Price Sheet API, when negotiated pricing is selected | Read customer subscription rates; retail fallback if unavailable |
| Azure Retail Prices API | Cost queries using region and service selections |
| Customer-configured GitHub repository, only if enabled | Uploaded code and review metadata |
| Package/provider registries | Customer-initiated dependency and provider installation |

Azure and Entra are Microsoft-operated services; Microsoft may process data as
their service provider under the customer's own agreements. This is distinct
from the platform publisher collecting application telemetry. Customer hosting
alone does not mean that no personal data is processed by any service provider.

## Acceptance checklist

Complete these in a customer-controlled non-production environment before
publishing executable workloads for employees:

- [ ] The app is reachable from the approved company network/VPN, and public network access is denied.
- [ ] Private DNS and SCM access work for the intended operators; image pulls and optional vault access have separate outbound connectivity.
- [ ] Unauthenticated browsers enter the company Entra sign-in flow; wrong-tenant identities are rejected.
- [ ] The configured initial administrator reaches Setup without an installation code; an ordinary tenant member cannot access administrator setup, even if they visit first.
- [ ] The primary connection is visible in Authentication, and no second provider is required to continue.
- [ ] The company GitHub OAuth App authorizes successfully, writable repository selection works, and Sync catalog now publishes reviewed inputs when enabled.
- [ ] Subscription IDs resolve to Azure display names and the correct tenant; saving outside-tenant or inaccessible targets fails without partial updates.
- [ ] Retail or negotiated pricing reports its actual source and any fallback; no quota checks run during initial setup.
- [ ] A disposable reviewed workload deploys and cleans up with the host managed identity, including failed-cleanup recovery.
- [ ] A restart retains setup, users, catalog versions and deployment records on persistent storage.
- [ ] Credential rotation, log/data retention, backups, restore testing and an operational owner are in place.

Local automated tests and UI previews do not establish live Entra, private
network or workload deployment readiness. An accepted deployment or a successful
image push is not a substitute for these checks.

## Updates and recovery

1. Build and scan a new customer image and record its digest. Retain the previous
   release so a reviewed rollback is possible. Do not overwrite release tags.
2. Back up protected host settings, role assignments and persistent application
   state before upgrading. Use a consistent SQLite backup procedure or stop the
   application for the backup; copying the live database alone is not sufficient.
   Include catalog versions, uploads and Terraform state as applicable.
3. Deploy using your customer change process. Keep the same persistent database,
   private ingress, Entra registration and identity unless an intentional migration
   has been reviewed. No first-run installer is needed for routine upgrades.
4. Recheck sign-in, administrator access, catalog operations and cleanup. A
   rollback must account for database/schema compatibility as well as image versions.
5. Rotate Entra credentials before expiry and verify Key Vault reference versions
   if used. Reauthorize GitHub after expiry or revocation. Disabling GitHub does
   not erase or revoke its saved token; revoke it in GitHub when no longer required.
6. Monitor failed deployments/cleanup, persistent storage usage and access to
   logs, SCM and backups. Keep one worker and one instance. App source changes
   require rebuilding/redeploying; catalog sync does not deploy application code.

`GALLERY_ADMIN_OIDS` is an initial bootstrap eligibility list, not a live role
synchronization mechanism. Manage existing portal roles through authorized admin
controls. Retain an approved recovery administrator and host access procedure;
do not delete the database or disable authentication to recover access.

Before decommissioning, review active deployments and their cleanup obligations,
revoke integrations, remove only the intended role assignments/resources, and
apply company data retention/deletion policy. Stopping the portal alone does not
delete deployed workloads or stop their Azure charges.

## Troubleshooting

| Symptom | Checks and safe next steps |
| --- | --- |
| Docker engine unavailable | Start the Linux engine and build for `linux/amd64`; an installed Docker command alone is not a running engine. |
| TLS handshake failure downloading Python packages | Docker does not inherit host pip settings. Pass the approved configuration through the optional `pip_config` BuildKit secret and review corporate proxy/trust policy. Never disable certificate checks or select an unapproved mirror. |
| Registry image pull denied | Check the exact host identity and its registry data-plane role, registry permission mode, image name/digest, routing and DNS. For ACR, AcrPull is for RBAC-only mode; ABAC mode requires the applicable repository reader role. |
| `Container startup blocked` | Check public access disabled, HTTPS, persistent storage, the attached identity/RBAC, enabled Entra provider, tenant-specific issuer, restricted audiences and GALLERY_ADMIN_OIDS. Do not replace the entrypoint to skip validation. |
| 401 at `/.auth/login/aad/callback` | Begin at the root URL, not the callback. Check the exact callback registration, tenant/client IDs, required ID-token issuance, credential expiry and Key Vault resolution, then review Easy Auth diagnostics and Entra sign-in logs. |
| Entra access/consent denied | Check enterprise-app user assignment, tenant membership, Conditional Access and company consent policy. Directory permissions, Azure roles and portal roles are separate. |
| Sign-in succeeds but Setup is forbidden | Check the user's object ID in this tenant and their persisted portal role. The first eligible configured user becomes initial admin; other employees and Azure Owners are not implicitly admins. |
| Private site cannot be reached | Check private endpoint approval, DNS from the actual client network, VPN routes and firewalls. Do not enable public ingress as a workaround. |
| Setup disappears after restart, or database is unwritable | Verify persistent `/home/data`, UID 10001 access and the expected database path. Stop and fix storage rather than create a second installation or change the process to root. |
| GitHub authorization or token storage fails | Check the customer OAuth client ID, device-flow enablement, organization policy/SSO, scopes, host write access and optional vault access. Reopen Setup after a settings-triggered restart; inspect partial repository creation before retrying. |
| Subscription lookup fails | Check Reader access for the host managed identity, not just the signed-in user; confirm the subscription is enabled and belongs to the configured tenant. |
| Negotiated pricing falls back to retail | Check supported billing agreement, API permissions and exact meter/currency/unit matching. Explicit fallback is expected when those conditions are not met. |
| Terraform is unavailable | The stock image does not include Terraform. Install and maintain an approved CLI in the customer image before enabling that executor; automatic download is disabled. |
| Local preview returns 405 on Save | Expected. The preview serves synthetic read-only data and does not authenticate real users or perform cloud operations. Test those on the secured Azure host. |

Collect only relevant status codes, timestamps and sanitized diagnostic details.
Do not include auth cookies, tokens, credentials or full app-settings/log dumps
in support tickets. Avoid repeated deployments while an image pull/build is
still running; inspect the recorded result first.

## Release and operational limits

The privacy-intake response is not a general release, security, licensing, or
export-control approval. Excluding IaC does not establish an export-control
exemption: software, cryptography, recipients and distribution locations may
still require review. Complete the applicable legal, open-source, security,
privacy and export reviews before external publication.

This is a starting distribution boundary, not production certification. Complete
threat modeling, penetration and abuse testing, dependency/license review,
retention controls, backup/restore tests, ingress verification and a live
customer-tenant deployment/cleanup test before enabling real workloads.