# Deployment Portal

Organization-operated Azure deployment platform. This branch contains the platform,
an empty catalog, and development tests/tools. Organizations provide all workload IaC.

**Engineering preview; not approved for external publication or production use.**

## Setup overview

**Approved organization source -> private company repository -> organization container
registry -> organization Azure App Service -> assigned-employee Entra sign-in -> administrator
browser setup.**

The default setup uses an organization-owned Entra app registration configured before
the site opens. No publisher identity, setup code or source-code edits are needed.
The interactive wizard provisions hosting, identity and host permissions using
the organization's operator account. Networking is entirely organization-owned: VPN,
private endpoints, DNS and VNet integration are optional and are neither created
nor required by the wizard or runtime. Existing network restrictions are preserved.
Public HTTPS does not mean anonymous access; company Entra authentication and
explicit employee/group assignment are required.

This README covers the supported sign-in-first container path. The
[organization operations guide](docs/OPERATIONS.md) also covers optional identity
providers, credential storage and alternative installation modes. That guide
becomes the root README in the exported organization repository.

- **[Step-by-step Azure deployment](docs/OPERATIONS.md#step-by-step-azure-deployment)**
- [Interactive deployment script](#interactive-deployment-script)
- [Deployment checklist (start here)](#deployment-checklist-start-here)
- [Prerequisites](#prerequisites)
- [1. Prepare the host](#1-prepare-the-host)
- [2. Configure company Entra sign-in](#2-configure-company-entra-sign-in)
- [3. Set application configuration](#3-set-application-configuration)
- [4. Build and deploy the image](#4-build-and-deploy-the-image)
- [5. Complete browser setup](#5-complete-browser-setup)
- [6. Verify before enabling workloads](#6-verify-before-enabling-workloads)
- [Updates and operations](#updates-and-operations)
- [Troubleshooting](#troubleshooting)
- [Local preview and testing](#local-preview-and-testing)

## Interactive deployment script

Use PowerShell 7+ with Azure CLI, from the repository root:

```powershell
./tools/Deploy-Portal.ps1 -Mode Prepare -WhatIf
```

The wizard uses your Azure CLI session to discover what already exists:

1. Reads `az account show` in the current terminal, displays the active identity
	and tenant, and defaults to that subscription. Other cached logins are excluded.
2. Offers resource groups, or lets you enter a new group name. Existing services
	are still discovered across the selected subscription, even for a new group.
3. Offers Linux apps, plans, registries and user-assigned identities with their
	group and region. Typed existing names are resolved too; duplicates require a
	group-specific selection. An existing app supplies its plan and configuration.
4. Detects the app's configured Entra registration and asks whether to reuse it.
	Otherwise it lists available single-tenant registrations in the active tenant,
	a client-ID entry, or Create.
5. Asks only for remaining details, then shows the create/reuse plan.

Use a **number** or listed value, **Enter** for the shown default, **N** for a new
resource or **Q** to cancel. A blank registry answer accepts a suggested name,
which is checked for availability. Region input accepts `Central US` or `centralus`;
existing hosting supplies it automatically. No CLI subscription default is changed.
There are **no network inputs or connectivity approvals**.

Selecting an app in another group asks to use its original group; the initially
proposed new group is then not created. Existing plans/registries/identities stay
in their original groups. New services are created in the final hosting group.
An existing app is never moved to a different plan. Preparation now checks Entra
and Always On readiness before apply. F1/D1 plans offer an optional paid upgrade
with shared-app impact review; there is no silent resize or automatic capacity guarantee.

The managed-identity choice offers the app's system identity, existing user-assigned
identities, or **New** for a named identity. A configured runtime identity is retained
and shown rather than silently switched. Other identity attachments are preserved.

Run the wizard in the same terminal/configuration as your Azure CLI login. If the
displayed identity is unexpected, cancel and inspect `az account show` there.
VS Code's Azure sign-in and separate terminals using another `AZURE_CONFIG_DIR`
are not substitutes for that CLI context. The wizard never changes the CLI default.

To start directly with a known group, add `-ResourceGroupName <group-name>` and,
optionally, `-SubscriptionId <subscription-guid>` to the command above.

After reviewing the plan, costs and backups, run without `-WhatIf`:

```powershell
./tools/Deploy-Portal.ps1 -Mode Prepare
```

When the preview is ready for reviewed preparation and Entra is not deferred,
it saves non-secret selections to `.portal/prepare.json` in the repository root.
The next command above automatically loads that file and skips resource-selection
questions. Azure readiness is checked again, and repair planning, change approvals,
secret entry, consent and image choices remain separate. No approval is stored.
The local file is the only preview write; no Azure changes are made.

Use `-Fresh` to choose again, or `-ConfigPath <path.json>` on both commands for a
separate plan. Explicit resource/identity parameters also start a new selection
flow rather than mixing targets with the saved file. Blocked previews and Entra
**Later** do not save or replace it. Keep configuration files out of source control;
the default `.portal/` directory is ignored and excluded from distribution exports.

The preview now returns `ReadyForApply`, `HostingReadiness` and `EntraReadiness`:
- **Blocked**: no preparation writes are allowed; resolve the reported issue.
- **Warning**: F1/D1 is retained without Always On; this does not block preparation
	or deployment, require upgrade approval, or bypass Entra checks.
- **ChangePlanned**: a supported hosting change, such as a selected F1-to-B1 upgrade
	or enabling Always On, will be applied only after confirmation.
- **RequiresRepair**: Entra has script-managed repairs, shown as `[PLAN]` after approval; choose whether to include the
	listed repairs. They receive their own confirmation on apply.
- **PendingCreation/Deferred**: new or deferred Entra configuration cannot yet be
	fully verified. It is checked after configuration before image deployment.

F1 supports Linux containers, but lacks Always On. The default **Keep** choice
allows preparation and deployment without upgrading or enabling Always On.
In-process jobs can be delayed while the app is unloaded; jobs running in GitHub
Actions are unaffected. This hosting choice does not move jobs between the site
and GitHub. An optional **Upgrade** shows shared-app impact and requires separate
confirmation before making a paid change. Supported paid tiers enable Always On.

Existing Entra preflight checks the tenant, exact callback, ID-token issuance,
enabled enterprise app, assignment requirement, direct initial-admin assignments
and credential metadata. Approved repairs preserve other callbacks/settings and
assign only enabled member administrators. They do not rotate credentials, alter
tenant/audience, bypass Conditional Access or sign anyone in.

It creates/reuses the group, one-instance Linux plan, Basic ACR (existing SKUs
retained), host managed identity and scoped roles. New apps require HTTPS and
remain stopped until configured. It enables Easy Auth on every path, creates or
connects Entra, then offers local Docker build/push or an existing image. Deployments
are pinned by digest. `-Mode Deploy` updates an already-prepared host.

Deployment verifies configuration (including Always On on supported paid tiers), starts the app, and makes one
bounded `/healthz` request. `HealthyResponse` means the app answered, not that the
exact running image or employee sign-in was proven. `SignInRequired` preserves the
authentication boundary; use your own signed-in browser to finish verification.

New enterprise applications require assignment. Only designated enabled member
administrator users receive initial app access; organizations assign other approved
employees/groups through Entra. Credentials go directly to protected host settings
and are not printed. No user sign-in or browser setup is performed by the script.

When reusing an Entra registration, the script may ask for its **client secret
Value**, the application's password, not your personal password or either ID.
The prompt explains how to find or arrange that value and enter it in the masked
terminal input. It is stored in protected App Service settings, not the local
Prepare configuration. See the [client-secret guidance](docs/OPERATIONS.md#interactive-deployment-script)
before continuing; never paste the secret into chat.

The scripts preserve organization network policy. If that policy blocks an actual
registry/API request or image pull, the organization must provide a permitted route;
the scripts do not bypass those controls. There is no VPN/private-endpoint readiness gate.

See [parameters and recovery](docs/OPERATIONS.md#interactive-deployment-script).

## Deployment checklist (start here)

1. Approve hosting cost, names, region and initial administrator user IDs.
2. Run the interactive preview, then confirm hosting preparation.
3. Create/reuse Entra and require enterprise-application user/group assignment.
4. Arrange tenant-wide administrator consent for the actual sign-in permissions.
	Follow [the consent prerequisite](docs/OPERATIONS.md#entra-pre-work-and-first-sign-in).
	Complete it before employee sign-in; user assignment and passing checks are not consent.
5. Build/scan/push the organization image and deploy its digest with managed-identity pulls.
6. After consent, sign in personally as the designated administrator and configure GitHub/subscriptions.
7. Verify unauthorized users are rejected, roles are respected, and state survives restart.

## Prerequisites

Organization Azure subscription/tenant, PowerShell 7 and Azure CLI; Docker with a Linux
engine for local builds; permissions to provision hosting and assign its roles;
directory permission to create applications and assign users when choosing Entra
Create. Azure subscription Owner does not itself grant directory authority.
An authorized Entra consent administrator must also be available to review and
grant tenant-wide consent before employee sign-in, even when reusing a registration.
For basic delegated sign-in scopes, a Cloud Application Administrator or Application
Administrator can do this; Azure subscription Owner and portal administrator cannot
grant consent solely by holding those roles.
Use approved organization source and an organization-owned registry. No network resources
need to exist before starting. Organizations manage any optional network restrictions.

## 1. Prepare the host

The wizard supports classic Linux App Service container hosting, one instance and
one worker. It grants the host Reader on the hosting subscription, Website
Contributor on the app, and registry image pull access. Workload deployment/cleanup
permissions and the build operator's registry push role remain separate grants.
Basic ACR is sufficient for the default authenticated-registry path; anonymous
pull and the registry admin account are disabled. Existing registries are not resized.

Existing app network settings are never opened, closed or overwritten. New hosts
use normal HTTPS endpoints behind App Service Authentication. Organizations may apply
private endpoints, VPNs, firewalls or other network policy independently.

## 2. Configure company Entra sign-in

Choose **Create**, **Existing** or **Later**. Create configures a single-tenant Web
registration, the actual `/.auth/login/aad/callback` URI, ID tokens, an expiring
credential and an assignment-required enterprise application. Only enabled member
administrator users are assigned initially. No extra Graph API consent is granted
to the runtime. Choosing Later prepares hosting but image deployment still requires
valid Entra authentication.

For Existing, configure the exact callback, ID tokens and tenant-specific issuer.
In **Entra > Enterprise applications > your app > Properties**, set **Assignment
required = Yes**. Under **Users and groups**, assign only authorized employees/groups,
including the designated administrator. Keep MFA/Conditional Access under organization
policy. App assignment permits sign-in, not portal administration; portal roles
are separately enforced. Do not assign external guests to an employee-only installation.

**Required before employee sign-in: administrator consent.** Assignment-required
apps do not allow users to consent for themselves. Have an authorized Entra
administrator use the app's sign-in flow, review the actual permissions and select
**Consent on behalf of your organization** before accepting. Standard Entra v2 Easy
Auth requests `openid profile email`; review any additional scopes on a reused app.
An empty API-permissions list does not eliminate consent for dynamically requested
scopes. Keep **Assignment required = Yes** and do not add broad Graph permissions
to dismiss **Need admin approval**. The wizard warns but neither grants nor verifies
consent. If the sign-in endpoint is not available until deployment, complete this
step immediately afterward, before employee use. Verify with the assigned initial
administrator's browser sign-in; an administrator-only successful login is not enough.

Both Prepare and Deploy now print **Administrator consent page**, a direct link
for the selected tenant and application, followed by **Portal sign-in**. The link
explicitly returns to the selected host's `/.auth/login/aad/callback`, even when
the registration is shared with another host; no hostname is hardcoded. The exact
callback must be registered. Open the
consent link as an authorized Entra administrator and review the permissions before
accepting. Once the app is running, use the portal link to sign in. The consent page
uses `/v2.0/adminconsent` with explicit `openid profile email` scopes, not all
configured API permissions. Review any additional permissions separately during
sign-in. A return to the OAuth callback does not establish a
portal session. The script does not open the browser, grant consent or verify the
approval; employee assignments and Conditional Access still apply.

Older static consent links can fail with `AADSTS1003031` when
`requiredResourceAccess` is empty. An empty list is valid for basic sign-in; the
updated v2 link requests those scopes explicitly without requiring extra Graph
permissions or registration changes. Use the new link instead of the old one.
`admin_consent=True` in an error URL is not evidence that consent succeeded.
See the [consent runbook](docs/OPERATIONS.md#entra-pre-work-and-first-sign-in).

The runtime verifies HTTPS, Easy Auth, tenant/client audience, host identity and
persistent storage. Neither public nor private network exposure blocks startup.

## 3. Set application configuration

In **App Service > Environment variables**, set:

| Setting | Value |
| --- | --- |
| `GALLERY_ADMIN_OIDS` | Initial administrator's user object ID, found in **Entra ID > Users > user > Overview > Object ID** in this tenant. |
| `WEBSITES_ENABLE_APP_SERVICE_STORAGE` | `true` |
| `WEBSITES_PORT` | `8000` for classic custom containers. For sidecar-enabled hosting, set the main container's target port to 8000 in its container configuration. |

Multiple initial administrator candidates may be comma-separated. The first
eligible sign-in becomes administrator when the database has no administrator;
other tenant users remain guests. Afterward, use portal administration to assign
additional administrators. Changing this list does not synchronize or revoke
roles already stored in the database.

Leave `GALLERY_INSTALLER_ENABLED` unset or `false`, and all `GALLERY_SETUP_*`
settings unset. **No setup secret is needed.** For the default container path,
also leave `GALLERY_ENTRA_TENANT_ID`, `GALLERY_ENTRA_CLIENT_ID`, `AZURE_CLIENT_ID`,
`GALLERY_HOST_RESOURCE_ID`, `GALLERY_PUBLIC_URL` and `ARM_TENANT_ID` unset unless
an advanced override is intentionally required.

The container discovers the host and Entra IDs, selects the system-assigned
identity, and defaults the database to `/home/data/deployment-portal.db`.
`GALLERY_SUBSCRIPTION_ID` may remain unset: add deployment subscriptions in Setup.
Optional branding uses `GALLERY_BRAND_NAME`. For a bound custom hostname, set
`GALLERY_PUBLIC_URL` to its exact HTTPS origin without a trailing slash and use
that same hostname in Entra's callback. An attached user-assigned identity can
be selected with `AZURE_CLIENT_ID`; do not mix identity modes.

The [configuration reference](site/server/.env.example) is documentation, not an
automatically loaded dotenv file. Do not copy blank overrides into host settings
indiscriminately. Keep secrets in the host secret store or Key Vault.

## 4. Build and deploy the image

Use the approved organization repository, not an archive of the development Git
history. Authenticate your build environment to your company registry using
its approved login/credential-helper process, then run:

```powershell
$image = 'registry.example.com/deployment-portal:release-1'
docker build --platform linux/amd64 --tag $image .
if ($LASTEXITCODE -ne 0) { throw 'Container build failed' }
docker push $image
if ($LASTEXITCODE -ne 0) { throw 'Image push failed' }
```

Replace the example registry and release tag with your own. If corporate policy
requires an approved Python feed, replace the build command with:

```powershell
docker build --platform linux/amd64 --secret "id=pip_config,src=C:\path\to\approved\pip.ini" --tag $image .
```

Docker does not inherit the host's pip configuration. This optional BuildKit
secret is mounted only during dependency download, not copied into the image.
Use your organization's configuration; do not bake feed credentials into build
arguments or disable TLS. When changing feeds, invalidate the dependency cache
with `--no-cache-filter dependencies`.

1. Scan the image and dependencies according to company policy and record its
	immutable registry digest. Maintain approved base-image/dependency versions;
	the supplied base tag and minimum-version dependencies can receive updates.
2. In **App Service > Deployment Center** (or your approved deployment tooling),
	select the organization registry, repository and release image. Prefer a digest
	reference where supported; otherwise use a non-reused release tag and record
	the resolved digest. Configure the host managed identity for image pulls.
3. Save and start/restart the app after the Entra and environment settings
	above are ready. Do not set a custom command that bypasses
	[the container entrypoint](site/server/container_start.py).
4. Check the deployment/build status and container startup logs. A pushed image
	or accepted deployment is not proof of successful startup. Allow for image
	pull/startup time; do not repeatedly redeploy solely because of an initial 5xx.

## 5. Complete browser setup

### First sign-in

Open the site's **root URL**, not the callback URL, through the organization's chosen access path.
Easy Auth redirects to company Entra sign-in. Sign in as the designated portal
administrator. Setup opens automatically when there are no deployment
subscriptions, starting on **GitHub**. Reopen it later from the account menu.

Authentication shows the existing primary Entra connection. Adding another
Entra/OIDC provider is optional and is not part of the default onboarding.
Ordinary users can sign in if allowed by company policy, but cannot modify Setup.

### Site branding

Choose your portal's name and footer in **Setup > 4. Branding**. Only portal
administrators can save changes. The site name updates the header and browser
title; the footer disclaimer supports your organization's own text (or a blank
value to hide it). Changes apply immediately and persist across image updates.
No new app registration, consent or rebuild is needed.

For deployment-time defaults, set `GALLERY_BRAND_NAME` (up to 80 characters) and
`GALLERY_FOOTER_TEXT` (up to 500 characters) in the host's environment settings.
Saved Branding settings take precedence. Use plain text; HTML is displayed as text.
Branding changes only presentation, not Azure resource names, Entra registrations,
GitHub repositories, permissions, or the MIT license/copyright notice.

### GitHub

**Administrator task.** Use the supported organization-owned **OAuth App with Device
Flow**, not a GitHub App installation. Follow the
[field-by-field OAuth registration guide](docs/OPERATIONS.md#register-the-organization-oauth-app)
and [repository-write permissions](docs/OPERATIONS.md#repository-writes-and-workflow-permissions)
for callback values, organization approval/SSO, token expiry, workflow-file access
and Actions configuration. OAuth authorization, Actions `GITHUB_TOKEN` and Azure
federation are separate credentials; granting one does not configure the others.

Other authorized employees remain portal `guest` users unless explicitly promoted
and need no GitHub authorization to consume or contribute use cases. **Every
authenticated portal user can submit Bicep/Terraform templates packaged as ZIPs**
for maintainer review. Setup and workflow management remain admin-only; submitting
does not automatically publish or merge code. See
[administrator, guest and contributor responsibilities](docs/OPERATIONS.md#administrator-and-guest-responsibilities).
This is a portal role, not permission to invite external Entra guests.

1. If repository integration is needed, register a **organization-owned GitHub OAuth
	App**, enable device flow, and satisfy your organization's app-approval/SSO
	requirements. This is not a GitHub App installation or a publisher-owned app.
2. In **Setup > GitHub > GitHub OAuth application**, enter its public client ID
	and select **Save OAuth application**. No host-setting edit is needed.
3. Enable GitHub, choose the token storage destination, and select **Connect GitHub**.
	Complete authorization at `https://github.com/login/device` using the code
	displayed by the site. Review the `repo`, `read:org` and `workflow` scopes. Do not paste
	access tokens into the site; they are retained server-side in the selected store.
4. Select an accessible writable repository, or explicitly confirm creating a
	private repository and seeding the organization source. **Connect an existing
	repository** saves the connection only. For an empty or LICENSE-only private
	repository, choose **Copy portal source to an empty or LICENSE-only repository**,
	select it, and confirm **Copy portal source**. The server verifies the contents
	and refuses to overwrite existing code. After copying, select **Sync catalog now**.

Host-setting changes during token storage can restart the app; reopen Setup to
resume. Disabling GitHub stops sync but does not revoke its token or delete the
last synchronized catalog. Revoke access in GitHub when it is no longer needed.
GitHub is optional; leave it disabled if not using this integration.

**Catalog processing defaults to the portal container.** It validates reviewed use
cases, refreshes retail pricing and collects configured Compute quota snapshots
without GitHub Actions or runners. In **Setup > GitHub**, choose **Catalog processing**,
enter capacity regions, save, and select **Sync catalog now** to queue a refresh.
Deployment subscriptions and host managed-identity Reader access are required for
quota collection. Progress and failures survive restarts; the last good catalog stays active.

**Optional runner prerequisites:** Actions policy must permit execution, and the
repository must have eligible GitHub-hosted runners or an approved online Linux x64
self-hosted runner with Git, Python 3.12, Azure CLI, Bicep and Terraform. Setup probes
access and disables unavailable/unverified runner choices. If access disappears,
pricing, capacity and static use-case validation fall back to the container.
No workflows are installed by default. Select verified runners before requesting
**Create workflow PR**. Runner capacity requires separate read-only Azure OIDC
federation; no identity, role or secret is provisioned automatically.

Container validation requires the packaged compilers and Linux Landlock/seccomp
isolation. It accepts local Bicep modules (no registry restore), validates Terraform
syntax without providers, and does not certify deployment feasibility. F1 remains
supported, but idle unload can delay background work. See the complete
[processing prerequisites](docs/OPERATIONS.md#catalog-processing).
See [Repository workflows](docs/OPERATIONS.md#repository-workflows) for triggers,
branch protection, local installation and configuration. App code changes require
an image rebuild; supported generated workload files sync into the existing image.

### Subscriptions and pricing

1. Grant the host managed identity Reader and the separately reviewed workload
	permissions on each intended deployment subscription. All subscriptions must
	be enabled and owned by the same Entra tenant as the host.
2. In **Setup > Subscriptions**, enter a subscription ID and choose **Add and save**
	(or press Enter). Azure verifies and saves it in one action. Change pricing in
	a saved row to save that preference automatically; failed updates restore it.
3. For bulk additions, expand **Import CSV**, select a file and choose **Import and
	save**. `subscriptionId` is required; optional `pricingMode` is `retail` or
	`negotiated`. Azure resolves names and tenants. Limits: 200 subscriptions and
	128 KiB per CSV. Failed lookups do not save partial lists.
4. Start with retail estimates unless your billing agreement and permissions
	support the negotiated-price API. Failed or ambiguous negotiated lookups
	explicitly fall back to retail; an estimate is not an invoice.

Adding a subscription does not grant permissions. Initial setup performs **no
quota checks**. The workload deployment view offers Compute quota checks later;
quota headroom does not guarantee physical capacity. Existing subscriptions
cannot currently be removed because tracked deployments may still require cleanup.

### Publish workloads

The initial catalog is intentionally empty. Publish reviewed organization-authored
runtime inputs through the configured repository and sync them. Bicep must be
compiled in the organization toolchain; the site does not compile it on sync. The
stock image has no Terraform CLI; install and maintain one in a reviewed organization
image before enabling Terraform execution. Uploaded workloads are executable code
with the host identity's permissions, not an untrusted-code sandbox.

See [repository input layout](docs/OPERATIONS.md#2-github) and
[workload publishing](docs/OPERATIONS.md#publish-your-own-workloads) for formats and limits.

## 6. Verify before enabling workloads

- [ ] The root URL loads under the organization's chosen network policy; no VPN/private endpoint is required by the application.
- [ ] Unauthenticated users are redirected to Entra; wrong-tenant and unassigned users are rejected.
- [ ] The designated administrator reaches Setup without a code; an ordinary tenant member cannot read or change administrator setup.
- [ ] GitHub authorization and catalog sync succeed if enabled; secrets are absent from responses and logs.
- [ ] Subscription lookup returns Azure names and the correct tenant; deployment targets outside the allowlist are refused.
- [ ] A reviewed disposable workload can deploy and be deleted/cleaned up with the managed identity's actual permissions.
- [ ] An app restart retains users, setup, catalog versions and deployment records. Test backups and restoration before production use.
- [ ] Costs, log retention, credential rotation and operational ownership have been approved.

Local tests do not establish live Entra or workload deployment
readiness. Perform these checks in an organization-controlled non-production environment first.

## Updates and operations

Build and scan a new image, retain the previous digest, back up persistent data
and protected configuration, and deploy through your organization change process.
Do not delete the database or rerun first-run installation for routine upgrades.
GitHub catalog sync does **not** update running application code. Rollback must
account for database compatibility as well as the previous image.

Keep one instance/worker, monitor persistent storage and failed cleanup jobs,
rotate Entra credentials and GitHub authorization, and restrict access to logs,
backups and SCM. SQLite backups must use a consistent backup procedure or be
taken while the application is stopped, not by copying a live database alone.
Data includes identities and deployment metadata; apply company retention and
deletion policies. Remove setup-related privileges only after reviewing ongoing
token-storage, provider-management and workload requirements.

To deliberately start over without deleting services, use the local
[reset script](tools/Reset-Portal.ps1). It detaches the old data and starts fresh
portal Setup while keeping Entra sign-in and Azure resources. Preview first with
`./tools/Reset-Portal.ps1 -WhatIf`. This is not an upgrade step: old workloads remain
billable and lose automatic cleanup until manually resolved. See the
[reset runbook](docs/OPERATIONS.md#reset-application-state) before applying.

## Troubleshooting

| Symptom | Check and safe next action |
| --- | --- |
| Docker cannot connect to its engine | Start Docker Desktop's Linux engine and confirm the build platform is `linux/amd64`. |
| TLS failure downloading Python packages | Use the approved pip configuration through `--secret id=pip_config,...`. Inspect corporate proxy/trust policy; never disable certificate verification. |
| Registry image pull denied | Check the exact host identity, pull role and registry permission mode, image digest/tag, outbound routing and DNS. Azure resource Reader is not registry data-plane pull access. |
| `Container startup blocked` | Check HTTPS, Easy Auth enabled with authentication required, persistent storage, managed identity/RBAC, tenant-specific provider and `GALLERY_ADMIN_OIDS`. Network topology is not validated by startup. |
| 401 at `/.auth/login/aad/callback` | Start from the root URL. Check the exact registered callback, tenant/client IDs, ID-token issuance, credential expiry/reference resolution, Easy Auth diagnostics and Entra sign-in logs. A callback opened directly cannot establish a session. |
| Sign-in works but Setup returns 403 | Check the user's object ID in this tenant and the stored portal role. Tenant membership or Azure Owner does not confer portal admin rights. |
| Organization network policy blocks access | Ask the organization network owner to verify their DNS/routing/firewall configuration. Existing restrictions are never changed by deployment. |
| Restart loses setup or database is unwritable | Verify persistent `/home/data` and UID 10001 permissions; stop and fix storage rather than create another installation. |
| GitHub authorization or save fails | Check organization OAuth client ID, enabled device flow, organization policy/SSO, requested scopes and host/Key Vault write permissions. Reopen Setup after a host restart. |
| Subscription lookup fails | Check the host managed identity's Reader access, subscription state and same-tenant ownership. Browser-user access is not sufficient. |
| Negotiated pricing falls back to retail | Check supported billing agreement, billing permissions and exact meter/currency/unit matching; fallback is explicit expected behavior. |
| Local preview rejects saves with 405 | Expected: it is read-only and uses a synthetic identity. Use the authenticated Azure host for real sign-in and cloud operations. |

Do not paste credentials, tokens, auth cookies or unredacted configuration/log
dumps into tickets. Share only the relevant status, timestamp and sanitized error.

## Local preview and testing

To inspect the UI without Azure, install Python 3.12 and run:

```powershell
python tools/preview_portal.py
```

Open `http://127.0.0.1:8766/`. The preview is loopback-only, read-only, and uses
synthetic users/data; it cannot test actual Entra sign-in or deploy resources.
The production image entrypoint intentionally refuses standalone local startup
without its required App Service environment. Do not disable production checks
or expose a preview server to make a local demo look like a hosted installation.

## Reference and license

- [Detailed organization setup and operations](docs/OPERATIONS.md)
- [App Service Entra configuration](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad)
- [Custom container configuration](https://learn.microsoft.com/azure/app-service/configure-custom-container)
- [App Service private endpoints](https://learn.microsoft.com/azure/app-service/overview-private-endpoint)
- [Registry managed-identity authentication](https://learn.microsoft.com/azure/container-registry/container-registry-authentication-managed-identity)
- [Key Vault references](https://learn.microsoft.com/azure/app-service/app-service-key-vault-references)
- [MIT License](LICENSE) and [security reporting](SECURITY.md)

The MIT License includes its AS IS warranty disclaimer and limitation of
liability. Retain its copyright and permission notices. No support SLA, trademark
rights or endorsement are granted; dependencies retain their own licenses.
Organization operation does not imply that no personal data is processed. Complete
legal, privacy, security and export reviews before external distribution or use.

## Local development

Use Python 3.12 and the repository virtual environment. Runtime dependencies are
declared only in [site/requirements.txt](site/requirements.txt).

```powershell
python -m pytest site/server/tests
python tools/build_distribution.py --output runs/organization-review.zip
```

The builder refuses to overwrite an archive. Extract the generated archive into
a new directory and run `python tools/preview_portal.py` from that directory
for a read-only browser preview. Actual hosting requires the organization-owned
authentication and managed identity configuration documented in the setup guide.

See [contributing](CONTRIBUTING.md) and the [release review checklist](docs/RELEASE-CHECKLIST.md).

## Distribution boundary

Bundled workloads, shared infrastructure modules, internal snapshots, old branded
assets and internal deployment workflows have been removed from this branch.
Tests, editor instructions, release notes and build tooling are development-only;
the export builder includes only the organization allowlist and empty data snapshots.
Local environments, credentials, databases, logs and preview artifacts are not
release inputs. No automatic workflow deploys this branch to an internal service.

**Deletion is not history removal.** The existing Git history still contains the
original content. Publish only an approved, history-free export into a new
repository; never publish a fork or mirror of this repository. The required
Microsoft copyright notice and MIT disclaimer remain intact.