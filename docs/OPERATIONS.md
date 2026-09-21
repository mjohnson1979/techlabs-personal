# Deployment Portal

Engineering preview of a self-hosted deployment catalog for your organization. Not approved for
external publication or production use. Review and approval are required before
external distribution.

## Setup sequence

The supported default is **organization hosting and Entra setup -> container deployment
-> assigned-employee sign-in -> administrator-only GitHub and subscription setup**. No
publisher registration, source-code edits or setup secret are required. The
code-protected installer is a separate opt-in alternative, not a default step.

**Deploying for the first time? Follow [Step-by-step Azure deployment](#step-by-step-azure-deployment)
in order.** It includes creating the registry, configuring Entra access, and moving
an existing Python app to the container. Networking is organization-owned, optional
and never a wizard/runtime prerequisite. Existing restrictions are preserved.
An app registration alone is not a hosting resource and does not complete these steps.

Use the [interactive deployment script](#interactive-deployment-script) to create
or reuse the hosting prerequisites, optionally publish the image, and apply its
App Service configuration. The runbook also provides the manual equivalent.

1. Complete [prerequisites](#prerequisites) and provision [hosting](#container-deployment).
2. Configure [Entra sign-in, administrator consent and the initial administrator](#entra-pre-work-and-first-sign-in).
3. [Build and deploy the image](#deploy-and-start-the-image) using your approved registry and package feed.
4. Complete [GitHub](#2-github) and [subscription/pricing](#3-subscriptions-and-pricing) setup in the browser.
5. [Publish reviewed workloads](#publish-your-own-workloads) and run the [acceptance checks](#acceptance-checklist).
6. Establish [operations and recovery](#updates-and-recovery); use [troubleshooting](#troubleshooting) for failures.

## Prerequisites

| Area | Organization prerequisite |
| --- | --- |
| Source and tools | Approved history-free organization source, Git, Docker with a running Linux engine/BuildKit, base-image access and an approved Python package feed. No local Python installation is required for Docker builds. |
| Azure | Azure public-cloud subscription/workforce tenant; permission to configure Linux App Service and registry with capacity/costs approved by the company. |
| Role assignments | A separately authorized operator can assign reviewed Azure roles. Contributor alone cannot grant role assignments. |
| Entra | Permission to create/manage a single-tenant Web registration, credentials and enterprise-app user assignments. Require assignment and select only authorized employees/groups. No Graph application-creation permissions are needed by the runtime. |
| Administrator consent | An authorized Entra administrator must review and grant tenant-wide consent for the actual sign-in scopes before employee sign-in, including for reused registrations. For basic delegated sign-in scopes, use a Cloud Application Administrator or Application Administrator. Azure subscription Owner and portal administrator are not consent roles. |
| Networking (optional) | Entirely organization-managed. VPN, DNS, subnets, private endpoints and VNet integration are not created, required or validated by this solution. Organizations remain responsible for service reachability if they add restrictions. |
| Registry | A company-owned image repository; build identity with push permissions and host managed identity with pull permissions. For ACR use AcrPull for RBAC-only registries or Container Registry Repository Reader for ABAC-enabled registries. |
| Runtime state | One worker, one instance, persistent `/home/data` writable by UID/GID 10001. The stock Linux x64 image includes Python 3.12, checksum-pinned Bicep 0.38.33 and Terraform 1.9.8. Review their licenses and updates. |
| Catalog processing | Portal container is the default: no GitHub Actions or runners required. Bicep/Terraform static validation requires a Linux kernel with Landlock and seccomp enabled; failure to enforce isolation blocks validation. |
| Optional runners | GitHub Actions must be enabled and the selected repository must have eligible GitHub-hosted runners or an approved online Linux x64 self-hosted runner. Actions enabled alone is not proof of runner access. See Catalog processing below. |
| Pricing and capacity | The app discovers regions for saved subscriptions. Retail pricing needs the public retail-price API; quota and service availability need the host managed identity's Reader/equivalent access. Negotiated pricing also needs agreement-specific billing access. No Azure roles are granted by processing. |
| Portal ownership | Initial administrator's user object ID in the hosting tenant, configured through GALLERY_ADMIN_OIDS. Tenant membership, app ownership and Azure roles do not automatically grant portal roles. |
| Optional integrations | Organization GitHub OAuth App with device flow; existing Key Vault with applicable read/write and network access; supported billing agreement/permissions for negotiated pricing. |

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

You may name your installation and customize its footer in **Setup > Branding**,
or set deployment defaults with `GALLERY_BRAND_NAME` and `GALLERY_FOOTER_TEXT`. Microsoft
product names appear only to identify dependencies and services, not as the
platform's brand. No logos or service icon bundle are included.

## What is included

- Platform application source and an empty catalog.
- A blank configuration example and a SHA-256 file manifest.
- No infrastructure templates: no Bicep, Terraform, compiled ARM templates,
  parameter files, Terraform state, or bundled sample workloads.
- No source repository history, inherited internal workflows, internal documentation, snapshots,
  application databases, logs, submissions, credentials, or deployment artifacts.

Use a new repository for this archive. Do not publish a fork, branch, mirror, or
archive of the originating internal repository: its history and other paths are
not part of this distribution. The source allowlist and known-content checks are
engineering safeguards, not proof that every confidential item has been identified.

## Hosting in your environment

### Container deployment

Clone the approved organization source repository into your company's private
repository, build the image, and push it to your own registry. Do not clone or
publish the originating development history. No source-code changes, bundled
workloads or publisher app IDs are required. **Organization-owned Entra sign-in is
configured as deployment pre-work**, before anyone opens the site.

From the organization repository root, authenticate to your registry through the company's
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
port 8000. Its embedded source seed allows an organization-owned repository to be
created from the browser without inheriting another repository's history.
Pin the deployed image by its registry digest after your security/license scan;
maintain your approved Python base-image digest and dependency versions for
repeatable production releases. The supplied Python base tag receives updates.

Configure the organization-owned **App Service for Linux custom-container host**:

| Hosting control | Required configuration |
| --- | --- |
| Network (organization choice) | Authenticated public HTTPS works without VPN/private endpoints. Organization-imposed restrictions are preserved; organizations configure and support them independently. |
| HTTPS | HTTPS-only on the site and HTTPS required in App Service Authentication. |
| Authentication | Easy Auth enabled with your single-tenant Microsoft Entra provider; require authentication on all paths and enterprise-app user/group assignment. Redirect unauthenticated browsers to that provider. Never expose the container directly. |
| Identity | Enable the system-assigned identity. Grant Reader on the hosting subscription for tenant verification and Website Contributor (or narrower equivalent) on this app for setup writes. |
| Registry | Organization-owned image; grant the host identity the appropriate image-pull role. Private registries require working outbound routing/DNS. |
| Storage | Set `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`; `/home/data` must be writable by UID 10001 and persist across replacement containers. Start with a fresh database. |
| Process | One instance; container target port 8000 (`WEBSITES_PORT=8000` for classic custom containers). Keep the image entrypoint, without a custom startup override. |
| Initial administrator | Set `GALLERY_ADMIN_OIDS` to the intended administrator's Entra object ID in the hosting tenant. This is a non-secret identifier, not an email or application client ID. |

Hosting controls and Azure permissions belong to your platform deployment;
they are not granted or relaxed by the application. A public endpoint is not an
anonymous endpoint: Entra authenticates users and assignment limits access to
approved employees/groups. The container verifies site identity, HTTPS, Easy Auth
and its attached managed identity before listening, but imposes no network topology.
Restrict setup access to designated portal administrators.

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
   for Easy Auth's hybrid flow. Keep the registration organization-owned.
2. In App Service Authentication, configure that registration with the issuer
   `https://login.microsoftonline.com/<tenant-id>/v2.0`. Require authentication on
   every path and HTTP 302 login redirection to Microsoft. Do not add other
   audiences: the app client ID is implicitly accepted, or may be listed explicitly.
   Store the provider credential in protected App Service settings or Key Vault;
   rotate it before expiry. In **Entra > Enterprise applications > your app >
   Properties**, set **Assignment required = Yes**. Assign the administrator and
   only approved employees/groups under **Users and groups**. Do not assign external
   guests to an employee-only installation. The runtime needs no Graph app-creation rights.
3. **Arrange administrator consent before employee sign-in.** Assignment-required
   applications also need **tenant-wide admin consent**,
   including for basic OIDC sign-in. Assigning users does not grant consent.
   An authorized Entra administrator (for example, a Cloud Application Administrator
   or Application Administrator for delegated sign-in permissions) must review the
   actual sign-in scopes and consent on behalf of the organization. Azure subscription
   Owner and the portal's administrator role do not grant this authority. Keep
   **Assignment required = Yes**; tenant-wide consent does not bypass user assignments.
   Use the app's sign-in flow, verify the tenant and application client ID, review
   the actual requested scopes, and select **Consent on behalf of your organization**
   before accepting. Standard Entra v2 Easy Auth requests `openid profile email`;
   review additional scopes on reused registrations rather than approving them blindly.
   An empty API-permissions list does not eliminate consent for dynamically requested
   scopes. The wizard's checks, **Continue** choice and user assignments do not grant
   or verify consent. If the sign-in endpoint is not available until image deployment,
   arrange the administrator now and complete consent immediately afterward, before
   employee use. Existing deployments do not need rebuilding just to grant consent.
   The script prints **Administrator consent page** with the selected tenant and
   app client ID, using
   `https://login.microsoftonline.com/{tenant-id}/v2.0/adminconsent?client_id={client-id}&scope=openid%20profile%20email&redirect_uri={encoded-callback}`,
   plus a separate **Portal sign-in** link. The callback is derived from the selected
   host: `https://{selected-host}/.auth/login/aad/callback`, URL-encoded in the link
   and printed separately for review. It must exactly match a Web redirect on the
   registration. A reused registration can retain other hosts' callbacks; the link
   explicitly selects this host rather than leaving the return URL unspecified.
   No consent link is printed until the host URL is known.
   Open the consent link yourself as
   the authorized administrator; no browser is launched or consent granted by the
   script, including in `-WhatIf` and unattended runs. This link explicitly requests
   only the basic sign-in scopes **openid profile email**. It does not use
   `/.default` or request every configured API permission on a reused registration.
   An empty `requiredResourceAccess` list is valid for this sign-in-only flow;
   no extra Graph API permission is needed just to make the link work. Review and
   consent separately to any additional scopes requested by the app's actual
   sign-in flow or other uses of the registration.
   Consent may return to a registered callback without creating a portal session.
   Use the portal root link after the app starts; if the consent/return page fails,
   verify the grant in Entra rather than treating the redirect as proof of success.
   **Recovering from `AADSTS1003031`:** older scripts printed the static
   `/adminconsent` endpoint, which can fail when the registration has no configured
   API permissions. Use the updated `/v2.0/adminconsent` link with explicit
   `scope=openid%20profile%20email` and the selected host's callback. Merely adding
   `redirect_uri` to the old static link does not resolve this case. No registration
   modification or image rebuild is required. Do not add unrelated API permissions
   to silence the error. If the updated link still fails, have the application
   administrator inspect the exact request and error against the registration's
   actual requirements. `admin_consent=True` alongside `error=...` identifies the
   consent flow, not a successful grant. Complete consent yourself and then use
   the portal root link to establish a new sign-in session.
   See [Microsoft's v2 administrator-consent guidance](https://learn.microsoft.com/entra/identity-platform/v2-admin-consent).
4. Set `GALLERY_ADMIN_OIDS` to the intended initial administrator's **user object
   ID in this tenant**. Multiple candidate IDs may be comma-separated; the first
   eligible sign-in becomes administrator when no administrator exists. Additional
   administrators are assigned through the portal's admin controls. Updating the
   candidate list does not revoke or synchronize existing database roles. This is
   separate from Entra directory roles and Azure resource roles: being a tenant
   member, application owner or subscription Owner does not automatically make
   someone a portal administrator.
5. Deploy/start the image after the Easy Auth, managed identity
   and storage controls in the hosting table are configured. No application
   source edits or separate copies of the Entra tenant/client IDs are needed.
6. After administrator consent, open the site's root URL through the organization-selected access path. Easy Auth redirects to
   company Entra sign-in and enforces the organization's sign-in policies. Use
   the root URL, not the OAuth callback as a bookmark. The callback only processes
   a valid sign-in response; opening it directly is not a sign-in test.
   If it shows **Need admin approval**, ask the authorized Entra administrator to
   open the app's sign-in flow, review the requested permissions, and select
   **Consent on behalf of your organization** before accepting. Standard Entra v2
   Easy Auth sign-in requests `openid profile email`; review the actual request
   because reused configurations can request other scopes. An empty configured
   API-permissions list does not mean that dynamically requested scopes need no consent.
   Do not add directory-management permissions or disable user assignment to dismiss
   the prompt. For dynamically requested scopes absent from API permissions, use
   the actual sign-in consent flow rather than approving an empty static list.
   Verify sign-in as the assigned initial administrator; a consent administrator's
   successful login alone does not prove the assigned user's access works.
7. After the designated administrator signs in, **Setup** opens automatically
   when no deployment subscriptions are configured. Review **Authentication**,
   then use **Next: GitHub** for the optional company OAuth application and
   repository, followed by **Subscriptions** and **Branding**. Authentication
   shows the existing primary connection; creating a second provider is not
   required. Other tenant users
   remain guests and cannot open or modify administrator setup.

Never use a publisher registration or the Azure CLI client ID for this application.
The local read-only preview does not simulate real Entra authentication or consent.

Reference: [Configure App Service Entra sign-in](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad).
Consent: [Troubleshoot consent issues](https://learn.microsoft.com/troubleshoot/entra/entra-id/app-integration/troubleshoot-consent-issues#perform-admin-consent).

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

Follow the runbook below from step 1. The earlier hosting table is a reference,
not a substitute for provisioning the resources. Do not run the optional
code-protected installer when following this sign-in-first path.

### Interactive deployment script

From PowerShell 7+ with Azure CLI, run the wizard at the repository root:

```powershell
./tools/Deploy-Portal.ps1 -Mode Prepare -WhatIf
```

The console separates discovery, hosting checks, Entra checks, consent, plan
review, hosting changes, Docker build/push, deployment and final verification.
Blank lines and cyan headings mark each phase. Labels remain readable in plain
text logs even when the terminal does not display colors:

| Label | Color | Meaning |
| --- | --- | --- |
| `[OK]` | Green | This specific check or configuration step passed, not a claim that all onboarding is complete |
| `[INFO]` | Gray | Context, selected resources or unchanged settings |
| `[WARN]` | Yellow (PowerShell warning stream) | Limitation or unverified condition; review before continuing |
| `[BLOCKED]` | Red | A required check failed; no further preparation writes may proceed |
| `[ACTION]` | Magenta | A decision or manual step for the operator |
| `[PLAN]` | Cyan | Work the script will perform after approval, not a request to change settings manually |
| `[SELECT]` | Cyan | Numbered choice; green marks its default option |

Docker build and push output remains native and unchanged, between dedicated
headings. PowerShell's confirmation prompts remain native too. **Yes to All**
does not approve separate image-publication or deployment confirmation scopes.
Formatted messages use host/warning streams; result objects remain available to
PowerShell callers, with a separate result-data heading after image deployment.

| Input | Purpose |
| --- | --- |
| Subscription | Read `az account show` first; offer only that identity's subscriptions in its active tenant, with the active subscription first/default; ignore cached defaults belonging to other logins |
| Resource group | Select a group or propose a new name; discovery still includes existing services in other groups of the active subscription |
| Web app and plan | Group-qualified existing Linux apps/plans or a new name; an existing app supplies its actual plan ID and region, even across groups |
| Region | Inherited from existing app/plan; otherwise enter a listed canonical/display name (`centralus` or `Central US`); the existing group's region is suggested |
| Registry | Existing registries across the selected subscription, or an editable generated name with an Azure availability check |
| Managed identity | Reuse the app's system identity, reuse a user-assigned identity from any group in the selected subscription, or create a named identity in the hosting group; configured runtime identities remain pinned |
| Plan SKU | New plans offer B1 (default), B2, B3, S1, P1v3 and F1 (Free; no Always On). Enter `F1` or choose its listed number. Existing F1/D1 defaults to Keep; optional paid upgrades require separate confirmation. |
| Entra repair approval | Existing registration readiness is checked before apply and again against the prepared host; explicitly approve listed callback/ID-token/assignment fixes before connection. New-host callbacks remain pending until the actual hostname exists. |
| Administrator user object IDs | Existing host settings are retained; otherwise an enabled signed-in member is suggested for explicit confirmation when Graph lookup is permitted |
| Entra Create/Existing/Later | Offer reuse of the selected app's provider, or list single-tenant registrations in the active tenant; choose an existing client ID or a new registration |
| Registration name and credential days | Create only; 7-180 days, default 90; schedule rotation |

Use a listed **number/value**, **Enter** for the shown default, **N** for a new
resource, or **Q** to cancel. Invalid menu choices are retried. New group and
service names are only plans until you approve the later change confirmation.
The active CLI identity and tenant are displayed before selection. Switching to
another account or tenant requires selecting that login in Azure CLI and rerunning;
an explicit `-SubscriptionId` cannot silently select a different cached identity.
No login or subscription change is performed by the wizard. Run it in the same
terminal as login; different `AZURE_CONFIG_DIR` values or CLI executables can use
different local profiles. Compare `az account show` in that terminal if the
displayed identity differs from your expectation.
If you know the group, skip its selection prompt:

```powershell
./tools/Deploy-Portal.ps1 -Mode Prepare -ResourceGroupName '<organization-group>' -WhatIf
```

Entra applications belong to a tenant, not a resource group. Discovery first reads
the chosen App Service's configured client ID. Without a provider, it lists visible
single-tenant registrations, following directory pagination (up to 2000 entries;
beyond that use an explicit client ID). Stored deployment IDs, matching names or
callbacks inform suggestions, but another registration can be selected regardless
of display name. Discovery never assumes a match is valid without later tenant,
callback, ID-token and employee-assignment validation.
Declining the configured provider offers a new web app and separate registration;
the original app/provider is not replaced. Directory lookup failures are shown as
unavailable discovery, not as proof that no registration exists. Resource-group
discovery/permission failures stop rather than guessing that creation is needed.

App/plan/registry and identity discovery spans the selected subscription. Each
choice shows its original group and region; duplicate names use full resource IDs.
If an existing app is selected after proposing a new group, confirm switching
the hosting target to its actual group. Nothing is moved, and the earlier proposed
group is not created. Cross-group existing plans, registries and identities are
reused in place and must not disappear before apply; the wizard will not recreate
a missing selected resource in another group. New resources go in the final host
group. The app/plan must share a region; registry and identity regions may differ.
Selecting an existing app retains its attached plan rather than migrating it.

Identity reuse requires permission to read and attach the selected identity, in
addition to the reviewed host role grants. The wizard preserves unrelated identity
attachments, persists the selected client ID, and grants host roles to that identity.
The configured runtime identity cannot be silently replaced. With no pinned identity,
choose **System**, an existing identity, or **New** for a named user-assigned identity.
Named identity creation registers Microsoft.ManagedIdentity only when necessary.
Use the initialization helper's `-PlanResourceId`, `-RegistryResourceId`,
`-ManagedIdentityResourceId` or `-NewManagedIdentityName` for explicit automation.
These remain restricted to the active deployment subscription; no networking is added.

F1 supports Linux custom containers, but not Always On. The default **Keep**
choice continues preparation and deployment with a non-blocking warning and no
paid upgrade. Always On is not enabled on F1/D1. In-process jobs can be delayed
while the app is unloaded; GitHub Actions jobs are unaffected. This hosting choice
does not install workflows or move existing jobs between the site and GitHub.
Choose the optional **Upgrade** only when you want a supported paid tier. The
wizard lists all apps sharing the plan, separately confirms the billable change,
verifies the SKU/instance count and enables Always On on the selected app. Changes to the
shared-app inventory or SKU after preview stop the upgrade. Other paid plans are
never downgraded or resized by this option. Capacity, organization policy and upgrade
authorization can still fail; no automatic rollback or repeated upgrade is attempted.

There are no network questions, VPN keys, subnet IDs or connectivity approval
checkpoints. The wizard does not register Microsoft.Network or write organization
network resources. It creates a Basic authenticated ACR by default; existing SKUs
and network policies are retained. Anonymous pull and the registry admin account
are disabled for new registries. Paid App Service tiers and ACR incur charges.

Preview performs Azure reads but no Azure writes, credential prompts, image builds
or Graph creation. When `ReadyForApply` is true and Entra is not **Later**, it saves
the resolved selections to `.portal/prepare.json` in the repository root. This
local JSON file is the only preview write. Non-blocking F1 warnings and planned
creation/repair steps can still be ready for reviewed preparation; saved selections
do not certify runtime or sign-in readiness.

After reviewing the plan, permissions, budget and backups:

```powershell
./tools/Deploy-Portal.ps1 -Mode Prepare
```

Prepare automatically loads the file, shows its path and reviewed target, and skips
the resource/identity selection questions. It re-reads Azure configuration and
checks the saved tenant before any changes. It still asks for repair planning and
change approvals, any required credential through hidden input, and later image
choices. Secret values, tokens, consent, repair approval, `-Confirm` and
`-NonInteractive` are never stored. Unattended apply still requires explicit
`-Confirm:$false`; unattended repairs still require `-RepairEntra` on
Initialize-Portal.ps1.

To select different resources, use `-Fresh`. A successful fresh preview replaces
the saved file; blocked previews or Entra **Later** leave any older file unchanged
and print that fact. Explicit selection parameters, such as `-AppName` or
`-SubscriptionId`, also start discovery without mixing in old saved choices.
Invalid or incompatible saved files stop reuse before Azure writes; use `-Fresh`
and review a new preview. A configuration is a reusable set of selections, not a
checkpoint that skips completed steps or an authorization to change resources.

For separate environments, use the same custom path for preview and apply:

```powershell
./tools/Deploy-Portal.ps1 -Mode Prepare -Fresh -ConfigPath ./.portal/test.json -WhatIf
./tools/Deploy-Portal.ps1 -Mode Prepare -ConfigPath ./.portal/test.json
```

The default `.portal/` directory is Git-ignored and excluded from Docker contexts
and clean distribution exports. Custom paths must also stay out of source control.
Although no secrets are stored, the file contains organization resource and user
object IDs. Protect it as operator configuration and never add credentials to it.

Preparation configures hosting, HTTPS, managed identity, protected settings and
Easy Auth, with Always On enabled and verified only on supported paid tiers.
It requires authentication even before a provider exists (401), and
stops newly created apps until final image deployment. Existing restrictions are
not relaxed or made more restrictive. No source edits or setup code are required.

**Entra Create:** the operator needs organization directory authority to create apps,
service principals, read initial administrator member/account status and assign
app access (including the applicable Graph AppRoleAssignment.ReadWrite.All authority).
Azure Owner alone does not grant these permissions. The wizard configures the
actual host callback, ID tokens and single-tenant audience, creates an enterprise
application with assignment required, and assigns only the designated enabled
member administrators. It writes an expiring credential directly to protected
App Service settings without printing it or saving it locally. No extra Graph
permissions/admin consent are granted to the runtime. No user is signed in.

**Entra Existing:** confirm the discovered organization client ID or enter another
when no provider is configured. The wizard checks the exact
callback, ID tokens, tenant, enabled enterprise app, assignment-required policy,
direct initial-admin access and credential metadata before preparation writes.
It never replaces a different provider. Preconfigure
`PORTAL_ENTRA_CLIENT_SECRET` in protected host settings (or as a resolved organization
Key Vault reference), or enter the value at the hidden terminal prompt. Ensure
the designated administrator is assigned to the enterprise app.

**When the script asks for a client secret:** this is the application's password,
used by App Service Authentication during Entra sign-in. It is not your personal
account password. The prompt appears when connecting an existing registration
and the selected App Service does not already have its credential stored.
Existing mode does not automatically create or reset client secrets.

1. Open **Microsoft Entra ID > App registrations** in the tenant printed by the
   script. Select the registration matching the printed **Application (client) ID**.
2. Open **Certificates & secrets > Client secrets**. The credential is the text
   from the **Value** field. **Secret ID** identifies the credential record;
   **Application (client) ID** identifies the registration. Neither ID is the
   password requested by this prompt.
3. Use a valid secret Value retained in your approved secret store. Entra displays
   the Value only when the secret is created; you cannot reveal it again later.
   If it is unavailable or expired, an authorized administrator can select
   **New client secret**, enter a description and an organization-approved expiry,
   and record the new **Value** immediately. Schedule rotation before expiry.
   Do not delete existing secrets that another application may still use.
4. At **Paste the client secret Value, then press Enter**, enter it directly in
   the terminal. The terminal masks the characters you type or paste. Never paste
   the value into chat, source code, command-line arguments or the Prepare JSON.

The script saves the supplied credential in the protected App Service setting
`PORTAL_ENTRA_CLIENT_SECRET` and configures Easy Auth to reference that setting.
It does not print the value or write it to local files, including your saved
Prepare configuration. "Not saved locally" does **not** mean it is discarded:
the App Service retains it for sign-in. Required approvals still apply.

Press **Ctrl+C** to pause while arranging the credential; hosting already prepared
remains intact. See Microsoft's [application credential instructions](https://learn.microsoft.com/entra/identity-platform/how-to-add-credentials#tab/client-secret)
for the portal procedure.

After choosing reuse, the wizard prints **Entra registration correctly configured**
only when every read-only configuration and app-access check passes. Otherwise,
it distinguishes planned script work from settings that need manual attention:
tenant mode, callback, ID tokens, enterprise-app enablement, assignment policy,
direct administrator access, host client/issuer/audiences and credential metadata.
Independent configuration problems are reported together; if a directory read
fails, the report identifies that stage and does not certify unread settings.

Operator read access is verified by successful Graph requests. HTTP 403 reports
the required `Application.Read.All` (or accepted higher permission) on the Graph
client plus a supported operator directory role; verify consent and tenant policy
with your administrator. These are operator/CLI permissions, not permissions to
add to the portal registration. Entra v2 OIDC sign-in does not require Graph
directory-management permissions on the portal registration. Host managed-identity
Azure roles remain separate. Required repair-write authority is reported as
**not verified** by read-only checks, not as already granted or definitely missing.
The success message does not certify admin consent, secret validity, Conditional
Access or employee sign-in. It includes an explicit admin-consent warning: the
wizard does not inspect or grant consent, and user assignment alone is insufficient.
Have an authorized Entra administrator review consent before employee sign-in.
An **ADMIN CONSENT** section follows successful Entra checks with the application
ID, available portal URL and the manual consent procedure. After hosting and
post-configuration checks succeed, interactive preparation presents a consent
checkpoint: **Continue** to image selection, or **Stop** with hosting prepared to
arrange administrator approval. Continuing is not consent and does not set any
consent/sign-in verification flag. Preview and unattended runs show instructions
but do not add this interactive checkpoint. No browser is opened automatically.
Pending host/provider checks never produce the success message. They appear as
**[PLAN] Script step after approval**, not as failed validation. The script will
discover the hostname after host creation, offer any missing callback repair,
then connect the provider and configure protected credential storage after
confirmation. A missing existing credential may still require hidden terminal
input from the operator. This validation itself does not change configuration.

Repairable findings appear as **[PLAN] Script repair after approval**, with wording
such as **After approval, the script will add Web redirect**. They do not ask the
operator to make the repair manually. At **Plan the listed Entra repairs?**, choose
**Yes** to let the script perform the listed repairs after separate confirmation:
append only this verified host's callback, enable ID tokens, require enterprise
app assignments, and add direct access for missing designated enabled member
administrators. Existing callbacks, group assignments and unrelated options are
preserved. Directory repairs require their own confirmation and readback.
Enabling assignment-required may revoke access for previously unassigned users;
review the impact on any other apps using this registration. A direct admin
assignment may be added even if that user already has access through a group.
Callback, ID-token and assignment-policy repairs use the operator's existing
directory application-write authority. `AppRoleAssignment.ReadWrite.All` and its
required directory role are mentioned only when designated administrator access
needs an assignment repair, not for a callback-only change. Provider connection
uses App Service configuration-write access. These messages describe permissions
used by the script, not proof that they are missing or a request to add permissions
to the portal application. Denied reads/writes and unsupported configuration appear
as **[BLOCKED] Needs attention**. Choosing Entra **Later** remains a deferred manual
step, not promised script work. Azure subscription roles alone do not authorize
directory changes. No credential reset, deletion,
extra runtime API consent or user sign-in is performed.

Approving a plan is not proof of completion. The script verifies repairs after
writing them; `-WhatIf` only previews Azure operations and never repairs, creates,
or connects Azure resources. It can save the local selections file described above.
Consent approval, required credential input and employee sign-in remain manual.

The preview ends with these structured fields:
| Field | Meaning |
| --- | --- |
| `ReadyForApply` | False if a hosting/Entra blocker remains or required repairs were declined; True permits only the reviewed provisioning steps, not production certification |
| `ConfigurationSaved` / `ConfigurationPath` | Whether this preview saved non-secret local selections, and their absolute path; false/null when blocked or Entra is deferred |
| `HostingReadiness` | Ready, ChangePlanned, Warning (F1/D1 without Always On; does not block apply), or Blocked (unsupported tier) |
| `EntraReadiness` | Ready, RequiresRepair, Blocked, PendingCreation, PendingHost or Deferred. PendingHost permits hosting preparation, not Entra connection or image deployment without the post-host checks. |
| `EntraChecks` | Safe check name, status and detail for every collected Entra finding, including passing and pending checks; excludes registration objects and credentials |
| `BlockingFindings` | Exact blocking check names and safe details, also repeated in the final apply exception; does not include non-blocking warnings or registration credentials |
| `EntraRepairPlanned` | Whether repairs were explicitly selected; WhatIf still sends no Azure writes and does not save repair approval |
| `RuntimeVerified` / `SignInVerified` | False in preview; user sign-in is never completed by the wizard |

Directory HTTP 401 is reported as a token/authentication blocker and can occur
after successful login due to Continuous Access Evaluation or Conditional Access.
Refresh the affected CLI session and test Graph access; persistent challenges
require tenant-administrator investigation. HTTP 403 indicates denied
directory authority. Declined repairs are reported as `Entra repair approval`;
review them and choose **Yes** at **Plan the listed Entra repairs?**, or correct
the registration yourself. A paid hosting upgrade does not resolve either issue.
An expired/missing credential or mismatched host issuer is
blocked for organization correction rather than silently rotating or replacing it.
Unexpired credential metadata cannot prove that the stored secret is the matching
one or that a Key Vault reference resolves; real sign-in remains necessary.

For explicit parameter-driven preparation, use the initialization helper:

```powershell
./tools/Initialize-Portal.ps1 -UpgradePlanSku B1 -RepairEntra -WhatIf
```

Remove `-WhatIf` only after reviewing the plan, budget and directory impact. Both
the shared-plan upgrade and Entra repair still ask for confirmation. For unattended
apply, explicit `-Confirm:$false` is the operator's approval; all required inputs
must also be supplied. `-PlanSku` alone still never upgrades an existing plan.

**Entra Later:** prepares hosting without claiming it is ready for sign-in. Final
deployment still requires properly configured authentication and assignments;
the wizard stops after hosting instead of continuing to image publication.

After preparation, select **B** to build/push with local Docker and then review
deployment, **D** to deploy a published image, or Enter to stop with hosting
prepared and deploy later. Docker must have a working Linux engine for B;
Cloud Shell can prepare resources without Docker, then an approved build machine
can publish the image. A build can use an approved pip configuration via BuildKit
secret. Cancellation/build failure stops deployment.

At the image prompt, enter both the repository name and tag, for example
`deployment-portal:latest` or `deployment-portal:release-1`, without the registry
hostname. `latest` alone is incomplete. For **D**, you can also enter
`deployment-portal@sha256:<64-character-hex-digest>`; a digest cannot identify a
new build. Invalid or empty input is retried before any pip-config prompt, build,
push or deployment. Enter **Q** at the image prompt to stop and retain the prepared
hosting. The complete image destination is shown before continuing; deployment
still resolves the selected tag to a pinned digest.

For a prepared host, use **Deploy**; it changes only container/settings and starts
the app, preserving identity providers, network policy and data:

```powershell
$deployment = @{
    Mode = 'Deploy'
    SubscriptionId = '<hosting-subscription-guid>'
    ResourceGroupName = '<app-resource-group>'
    AppName = '<app-name>'
    RegistryName = '<acr-name>'
    Image = 'deployment-portal:release-1'
    AdminObjectIds = @('<administrator-user-object-guid>')
}
./tools/Deploy-Portal.ps1 @deployment -WhatIf
./tools/Deploy-Portal.ps1 @deployment
```

Registry image-metadata/push/pull requests still need actual service reachability.
If an organization restricts its ACR, App Service or Key Vault, the organization supplies
the permitted route. The solution neither verifies a VPN/private-endpoint topology
nor bypasses organization policy when a service call fails.

Use `Get-Help ./tools/Initialize-Portal.ps1 -Full` for preparation parameters.
Unattended preparation requires all inputs and an explicit `-EntraMode`; apply
also requires `-Confirm:$false` after change approval. It does not build or deploy
an image unattended. No secret command-line parameters exist. Do not trace or
transcribe scripts when they handle protected settings.

**Recovery:** ARM creates may return `Creating` before a resource is ready. During
resource creation/reuse, the wizard waits up to ten minutes using read-only status
checks, then verifies `Succeeded` before continuing. It also waits for matching
resources still provisioning from an earlier run, without sending another create.
If the wait times out or provisioning fails, inspect Azure and rerun with the same
names after resolving the issue. Do not delete partial resources just to retry.
Completed matching resources are reused immediately. Retained
Entra registrations/credentials are not reset or deleted; unrelated same-name
applications are not adopted. If credential storage failed after issuance, inspect
the registration and remove unused credentials through organization rotation procedures.
**New host with a reused Entra registration:** the preview may report
`Entra callback [Pending]` because the exact hostname does not exist yet. After
hosting is prepared, the wizard rechecks the actual hostname before connecting
the provider. If the registration needs a new callback, choose **Yes** at
**Plan the listed Entra repairs for the prepared host?**, then review the separate
repair confirmation. Repairs append the new callback and preserve existing Web
redirects and credentials; they do not replace the old portal's callback or grant
administrator consent. Unattended runs need explicit `-RepairEntra` approval.

If an older script stopped after **Hosting prepared** with **Fix the single-tenant
Entra registration, exact callback and ID-token issuance checkbox**, keep the
already-created resources. Rerun the updated Prepare wizard and select the same
resource group, app, plan, registry and existing registration. With the host now
present, it can show the exact missing callback in the preflight repair list.
Approve only the intended changes, or add that Web redirect yourself under
**Entra > App registrations > your registration > Authentication**. A new host
may also need the existing registration's credential configured through the secure
prompt or a protected host setting; the wizard does not copy another app's secret.
Declined, failed or unverified repairs retain hosting and stop before provider
connection/image deployment. Do not delete the resources or reset application data
to recover from a missing callback.

Settings writes are not atomic and may restart the app; retain backups and avoid
concurrent changes. No automatic destructive rollback is attempted.

`HostingPrepared` and `ConfigurationApplied` report accepted configuration, not
successful employee sign-in or workload execution. Deployment accepts F1 without
Always On, enables/readbacks Always On only on supported paid tiers, starts the app and issues one
30-second, no-redirect `/healthz` request. It returns `HealthObservation`:
`HealthyResponse` for the expected JSON health reply, `SignInRequired` when access
is gated, or `PendingOrFailed`/`Unverified` for unsuccessful checks. It never sends
credentials, follows login redirects or opens network access to pass the check.
HealthyResponse is not proof of the exact running image; verify the pinned digest
in container startup logs. Employee sign-in and workload acceptance remain user-run.
The result also includes `HealthDetail`, the safe explanation for the observation.
The **DEPLOYMENT RESULT** section distinguishes accepted configuration from pending
runtime, consent and sign-in checks. **NEXT ACTIONS** lists startup-log verification,
administrator consent, assigned-employee sign-in and browser Setup. `Unverified`
alone does not mean the deployment failed or that the image should be rebuilt.

To recheck health after a slow image pull without redeploying:

```powershell
. ./tools/Deploy-Portal.ps1
Test-PortalDeploymentHealth -Origin 'https://<actual-app-hostname>'
```

Open the root URL yourself to verify sign-in and administrator Setup. Do not
disable authentication or add excluded paths just to make a health probe succeed.
Offline mocked tests do not certify live cloud operation.

### Step-by-step Azure deployment

1. **Choose the organization target.** Record subscription, region, group, app/plan/ACR
   names and initial administrator user IDs. Approve App Service/ACR cost and backups.
   Networking need not be built before deployment or setup.
2. **Preview then prepare.** Run the interactive commands above. Reuse compatible
   hosting or create a new Linux one-instance plan/app and authenticated registry.
   The host receives Reader on the hosting subscription, Website Contributor on
   the app and registry pull access. Workload and build-operator permissions are separate.
3. **Configure employee access.** Choose Create or Existing for Entra. Require
   assignment in the enterprise application's Properties. Assign approved employees
   or groups under Users and groups, excluding external guests. Only designated
   administrator IDs can bootstrap the first portal admin. Review MFA/Conditional
   Access under company policy; enterprise assignment does not confer portal admin.
4. **Arrange administrator consent (required before employee sign-in).** Follow
   [Entra pre-work and first sign-in, step 3](#entra-pre-work-and-first-sign-in).
   An authorized Entra consent administrator reviews the actual sign-in scopes and
   selects **Consent on behalf of your organization**. Assigning users and passing
   configuration checks do not grant consent. Keep **Assignment required = Yes**.
   If the sign-in endpoint is not available yet, complete consent after step 6
   and before step 7. Do not enable employee use while consent is pending.
5. **Build and scan the image.** Use the optional wizard build or the commands in
   [Container deployment](#container-deployment). Grant the build identity AcrPush
   for RBAC ACR or the applicable repository writer role for ABAC. Use a unique
   release tag, scan dependencies and record the digest. No publisher registry is used.
6. **Deploy the image.** Choose D or `-Mode Deploy`. The script resolves the digest,
   enables managed-identity pulls, clears startup overrides, configures port 8000
   and persistent storage, then starts the app. Check container logs and actual health.
7. **Verify consent and sign in personally.** Open the root HTTPS URL, not the callback, and complete
   Entra sign-in as the assigned designated administrator. Unassigned and
   wrong-tenant users must be denied. No network setup screen precedes this step.
   If **Need admin approval** appears, return to step 4; do not rebuild the image
   or disable assignment restrictions to bypass it.
8. **Complete Setup.** GitHub is optional and organization-owned. Add subscription IDs
   or CSV; Azure resolves names/tenants. No quota checks run during initial setup.
9. **Verify operation.** Use the [acceptance checklist](#acceptance-checklist),
   test restart/persistence and backup restore, then deploy/clean up a reviewed
   disposable organization workload before enabling wider use.

Reference: [App Service Entra authentication](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad)
and [enterprise application assignment](https://learn.microsoft.com/entra/identity/enterprise-apps/assign-user-or-group-access-portal).

### Local read-only preview

Extract the organization archive into a new directory. With Python 3.12 installed,
run this from the extracted root (no packages or cloud credentials are needed):

```powershell
python tools/preview_portal.py
```

Open `http://127.0.0.1:8766/` in your normal browser. Use `--port 8767` if the port
is occupied. The launcher binds only to loopback and serves the exported UI with
a synthetic administrator and empty deployment records. It does not import the
production backend, create a database, authenticate a real user or contact Azure.
Writes, uploads, role changes and deployments return a read-only error. The empty
catalog contains no sample infrastructure. Stop with Ctrl+C. Never expose or
tunnel this preview server; it is not an authentication or deployment test.

When running the launcher from the development repository instead, pass
`--root <path-to-extracted-organization-archive>`. The launcher requires the organization
distribution marker and cannot preview the internal source tree by default.

### Configuration preflight

Set your organization environment settings, then run from the extracted root:

```powershell
python site/server/distribution.py --check
python site/server/distribution.py --check --json
```

Both commands check the same rules enforced at application startup, using only
the Python standard library. They report all invalid/missing settings, print no
configured values, perform no network calls and write no files. Exit code `0`
means local settings passed; `1` means configuration problems; `2` means invalid
command arguments. `--json` provides structured output for organization CI.

This standalone check does not perform the container's Azure discovery. When
using it outside the container, supply the explicit tenant/client IDs as well as
the other required configuration; the deployed container discovers those IDs
before applying the same validation. Missing IDs in an unconfigured local shell
are therefore expected, not a reason to disable validation.

The example environment file is not automatically loaded. A passing result does
not verify resource ownership, tenant membership, Easy Auth ingress, RBAC or
release approvals. Use your actual organization configuration for deployment checks,
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

Provision an organization-owned Linux App Service, persistent fresh database and
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

This version supports one organization Azure tenant and multiple deployment subscriptions,
hosted on organization-owned Azure App Service for Linux with App Service
Authentication (Easy Auth). Self-hosted OAuth, external-subscription authorization,
automatic guest invitations, and Terraform auto-download are unavailable.

1. Provision your own App Service, persistent storage, single-tenant Entra web app
   registration, and user-assigned managed identity. No hosting IaC is supplied.
2. Configure App Service Authentication to require authentication for **all**
   requests. Use your tenant-specific v2 issuer and only your app's client ID as
   the allowed audience. Configure the web redirect URI as
   `https://<your-host>/.auth/login/aad/callback`. Disable token storage unless
   needed for another organization-approved purpose. Configure authentication
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

Use a fresh, organization-controlled database for every installation. Do not import
an internal deployment database or reuse it between tenants. The current storage
and scheduler design requires **one worker and one app instance**. Scale-out and
multi-tenant hosting are not supported. Confirm sign-in with your tenant, denial
of other tenants, administrator assignment, deployment, and cleanup in your own
non-production subscription before use.

The `site/distribution.json` marker permanently selects the organization restrictions
for this package, even when the environment flag is omitted or set to `internal`.
Startup rejects missing or inconsistent organization configuration.

## Hosted initial setup

The **Setup** entry in the administrator account menu opens a four-step sequence:
Authentication, GitHub, Subscriptions and Branding. It opens automatically when an administrator signs in
and there are no deployment subscriptions. Every setup endpoint requires an
authenticated administrator; writes require a same-origin browser request.

Back, Next and the named step tabs retain unsaved field values while the panel
stays open. Navigation does not save, create a repository or finish installation.
Use each step's explicit save actions before closing; closing discards remaining
drafts. The footer states the next action, and step labels describe saved state,
not whether the step was visited. Navigation pauses during saves and GitHub device
authorization; finish or cancel the operation before moving on. Optional catalog
processing and advanced credentials remain collapsed until needed.

The GitHub section is numbered separately: **1. Save OAuth application**,
**2. Authorize GitHub account**, **3. Save repository connection**, then
**4. Catalog processing (optional)**. Dependent groups remain visible but grayed
out and disabled until the previous prerequisite succeeds. Failed requests do
not unlock later groups. Editing a prerequisite locks its dependent groups again
until successfully saved or verified. Existing saved configuration is recognized;
account access is rechecked when the GitHub section opens.

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
- Registration automation requires organization-admin-consented Microsoft Graph
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

Key Vault storage uses an **existing vault in the organization Azure tenant**, not a
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

#### Administrator and guest responsibilities

All registration, connection, repository selection, workflow installation and
credential-management procedures in this section are for administrators. Other
authorized employees use the portal with its `guest` role to browse use cases,
submit use-case templates, view estimates and deploy/manage their own workloads
within the configured limits.
They do not need GitHub accounts, repository access, OAuth authorization or Azure
credentials in the browser to consume the catalog or submit templates for review.

| Responsibility | Required authority |
| --- | --- |
| Open Setup, connect/disconnect GitHub, select/create a repo, request a workflow PR or force catalog sync | Portal `admin` role; enforced by the backend, not just hidden controls |
| Register/maintain the company OAuth App or approve organization OAuth access | Appropriate GitHub account/organization administration authority |
| Authorize the token used by the portal | Organization GitHub account allowed to read/write the selected repo; organization repository-creation rights if creating one |
| Review and merge proposed code/workflows; configure Actions policies | Organization GitHub maintainers/admins with the corresponding repo permissions |
| Configure scheduled capacity's federation and Reader role | Organization Azure/Entra administrators with authority over that identity and scope |
| Consume published use cases | Assigned, authenticated employee with portal `guest` role; deployment ownership/limits remain enforced |
| Submit Bicep/Terraform use-case templates in a ZIP | Any authenticated portal user, including `guest`; no portal admin role or personal GitHub connection required |

These authorities are separate. GitHub organization owner, Entra administrator
and Azure subscription Owner are not automatically portal administrators. In a
fresh organization installation, only a user listed in `GALLERY_ADMIN_OIDS` can become
the initial portal administrator; other users remain guests unless a portal
administrator explicitly promotes them. Keep the administrator group small.
The portal role `guest` means a limited application user, **not an Entra B2B
external guest**; retain the employee-only Entra assignment policy.

**Open contribution is intentional:** every authenticated portal user, including
guests, can submit a use-case template packaged as a ZIP. Submissions are staged
for maintainer review and, when GitHub is connected, can create an intake branch
using the server-side token. Contributors do not need repository write access.
Submission does not automatically publish, merge or execute the template. The
portal guest role grants no merge authority and cannot call admin workflow or
configuration endpoints; publication requires review and the appropriate GitHub
repository permissions. Do not restrict submissions to administrators.

#### Choose the supported GitHub application

The default remains an organization-owned **GitHub OAuth App with Device Flow**.
The credential layer also accepts GitHub App device-flow **user** tokens and
renews them when GitHub supplies a refresh token. This is not installation-token
authentication: it still depends on the authorizing user's access and the app's
repository grants. Installation-token issuance, private-key authentication and
automatic installation/permission configuration are not implemented. App IDs,
installation IDs and private keys are not client IDs. Features requiring OAuth
scopes (such as the workflow-file scope check) may remain unavailable with GitHub
App user tokens; this does not bypass the repository's access rules.
GitHub.com (including organization-managed accounts subject to policy) is the
configured API host; GitHub Enterprise Server custom hosts are not supported.

#### Register the organization OAuth App

1. Sign in to GitHub using the organization account authorized to manage the app.
   Prefer organization organization ownership for continuity. For an organization,
   open **Your organizations > your organization > Settings > Developer settings >
   OAuth Apps**. For a personal-owned registration, use **Settings > Developer
   settings > OAuth Apps**. Choose **New OAuth App** (or **Register a new application**).
2. Complete the registration fields below. Use organization-owned URLs and names;
   GitHub cautions that app registration metadata should be treated as public.
   Do not include confidential internal URLs, tokens or resource identifiers.
3. Select **Enable Device Flow**. Decide the token-expiry policy with your security
   administrator, then select **Register application**. Reopen the registration
   and verify Device Flow remains enabled and callback wildcard matching is off.
4. Record the **Client ID** for portal Setup. It is public configuration, not a
   secret. Do not generate or copy a client secret: this device flow does not use
   one. No webhook URL, webhook secret, GitHub App private key or installation ID
   is needed.

| Registration field | Value for this portal |
| --- | --- |
| Application name | A recognizable organization name, for example `Company Deployment Portal` |
| Homepage URL | The organization portal's HTTPS root URL if suitable for registration metadata; otherwise an organization-approved public information URL |
| Application description | Optional non-sensitive description of the administrator repository integration |
| Authorization callback URL | The portal HTTPS root URL, such as `https://portal.example.com/`, or an approved organization-owned HTTPS placeholder if the portal URL is confidential. It is a required registration value but **unused by this device flow**. It does not add a callback handler to the portal. |
| Callback wildcard matching | Disabled; unnecessary for this integration |
| Enable Device Flow | Enabled; required |
| Expire user access tokens | Follow company policy. When GitHub supplies a refresh token, the portal renews access automatically before use near expiry or after idle time. Expiry is still enforced; disabling it is not necessary. Tokens without refresh support require reconnection if they expire or are revoked. |

Do not use Entra's `/.auth/login/aad/callback` for the GitHub callback. GitHub device
authorization finishes at `https://github.com/login/device`, not through the
registration's callback. Entra controls employee sign-in to the portal; GitHub
authorization controls the portal's repository integration.

#### Authorize and connect the repository

1. A portal administrator opens **Setup > GitHub > GitHub OAuth application**, enters
   that OAuth App's **Client ID**, and selects **Save OAuth application**. No host
   setting edit is required. Legacy `GALLERY_GITHUB_OAUTH_CLIENT_ID` remains a
   fallback only when no client ID is saved in Setup.
2. Enable GitHub, review **Token storage (advanced)**, and select **Connect GitHub**.
   On GitHub's device page, enter the displayed code yourself, verify the organization
   OAuth App name, and authorize with the intended GitHub account. Check the
   requested `repo`, `read:org`, and `workflow` scopes. OAuth scopes are requested
   during this authorization; they are not repository-permission checkboxes in
   the OAuth App registration form.
3. If organization approval or SSO is required, have the GitHub organization owner
   approve this OAuth App under the organization's **Third-party access / OAuth
   app policy** and complete the required SSO authorization. Being a portal admin
   or seeing the organization in discovery does not bypass that policy. Use a
   company-approved, least-privileged GitHub operator account: `repo` is broad and
   the token can access more repositories than the one selected in portal Setup.
4. After authorization, return to the portal and wait for account/repository
   discovery. Select **Connect an existing repository** to save only the connection,
   or confirm **Create a private repository and copy source** to seed a new one.
   To recover an empty or partially initialized repository, select **Copy portal
   source to an empty or LICENSE-only repository**, choose that repository, and
   press **Copy portal source**. Verify the owner and branch in the confirmation.
   The server accepts only an active private repository with write access whose
   selected branch is empty or contains only the exact bundled MIT LICENSE. A
   repository already matching the complete bundle is verified without new writes.
   Other existing code, modified licenses, symlinks and submodules are rejected.
   The existing repository's default branch is selected; protect the catalog branch.
5. In **Catalog processing**, keep **Portal container**, or use **Check runner
   access** and select a verified runner option. Unavailable/unverified choices
   are disabled. All Azure regions are discovered automatically for each saved
   subscription; no region list is required. Select **Save processing settings**. No quota request runs in this save
   request; processing is queued separately. New source copies contain no workflows.
6. Open or refresh the portal after reviewed files are merged into the configured
   branch. The use-case list updates automatically when synchronization completes.
   It queues work and displays queued/running/succeeded/failed status. Container
   mode validates inputs and refreshes prices and configured quota snapshots;
   runner mode imports workflow-generated outputs. F5 reloads the saved catalog.
   If using runners, **Create workflow PR** installs their optional automation
   through review, not an automatic merge or app deployment.

Host token storage can restart the app; reopen Setup afterward. Never paste
access tokens, client secrets or browser cookies into the portal, logs or tickets.
The device code and OAuth token remain server-side; the browser gets only the
verification code, status and discovered account/repository metadata.

Existing connections established by older images did not retain refresh tokens.
After upgrading, reconnect once in **Setup > GitHub** to enable automatic renewal
when GitHub provides renewal credentials. An image update cannot recover a refresh
token that was previously discarded. The browser shows **Automatic renewal enabled**
for a connection saved with renewal credentials.

If catalog sync reports that GitHub authorization is unavailable, a portal
administrator must use **Setup > GitHub > Connect/Reconnect GitHub** and complete
device authorization again. Keep the existing repository and branch; there is no
need to recreate the repository, copy source, re-upload workloads or merge again.
After reconnection, refresh the portal. Missing or expired credentials and GitHub
HTTP 401 responses are reported as reconnection required; permission and rate-limit
failures remain separate errors. Regular users are directed to an administrator.

Same-account renewal can keep the repository enabled. Before replacing the stored
credential, the portal verifies the saved numeric GitHub account ID, OAuth
application, repository write access and configured branch. It preserves the
repository settings and queues a catalog sync only after credential storage
succeeds. To switch GitHub accounts, explicitly disable the repository first.

Older portal images reject device sign-in while a repository is enabled. On those
images, uncheck **Enable GitHub** and select **Save GitHub as disabled**, then check
**Enable GitHub** again and connect. Choose **Connect an existing repository**,
reselect the same repository, save, and refresh. Disabling does not delete the
repository or its files, but it clears the saved repository selection; record the
configured branch before disabling, especially when it differs from the default.

#### Repository writes and workflow permissions

| Permission/credential | Used by | Purpose and limits |
| --- | --- | --- |
| OAuth `repo` | Portal backend acting as the authorizing GitHub user | Read private catalog files; create repositories when the user is allowed; write blobs/trees/commits/review branches and create workflow PRs. Broad user-level scope, not restricted by GitHub to the selected portal repo. |
| OAuth `read:org` | Portal backend | Discover the user's organization membership and available owners; does not grant organization administration or repo-creation rights. |
| OAuth `workflow` | Portal backend | Required only when adding/updating optional `.github/workflows/` files through a workflow PR. Container processing and normal source copying do not require it. The device authorization currently requests it for optional automation. |
| Per-job `GITHUB_TOKEN` | GitHub Actions runner, not the portal | Generated automatically for a workflow run. The supplied build jobs use `contents: read`; separate publication jobs use `contents: write` to commit generated outputs. Do not copy it into App Service. |
| OIDC `id-token: write` | Scheduled capacity job | Obtains a GitHub identity assertion for organization Azure federation. It is not a repository-write permission or an Azure role grant. Configure the separate capacity prerequisites below. |

The portal uses its stored OAuth token for writes; the visitor's Entra token and
App Service managed identity cannot authorize GitHub commits. Scope grants never
exceed the authorizing GitHub user's own rights. New-repository creation additionally
requires permission to create repositories under the selected owner. The portal
requests no `delete_repo`, `admin:org` or package-publication scope.

Repository administrators should check **Settings > Actions > General** to ensure
the organization policy permits the supplied SHA-pinned actions and job permissions.
Allow the publication job's explicit `contents: write` only under reviewed policy;
do not enable blanket repository write access for unrelated jobs. The GitHub
setting **Allow GitHub Actions to create and approve pull requests** is not needed
for the portal's **Create workflow PR**: that uses the OAuth user token, not
`GITHUB_TOKEN`. The supplied Actions jobs publish generated files and do not
create or approve PRs. Neither setting bypasses branch protection or rulesets.

Source creation/recovery writes to the selected branch after confirmation; workflow installation proposes
an isolated review branch/PR. Catalog uploads create intake branches for human
review. No automatic merge is performed. Rules requiring reviewed/signed commits
or restricting branch creation may block these operations even with every OAuth
scope. Follow the organization's approved import/publication path rather than disabling
protection. GitHub branch write permission also does not grant portal admin rights.

#### Administrator acceptance checks

- Verify the intended GitHub account/organization owns the OAuth App and repository,
  Device Flow is enabled, and organization approval/SSO is complete.
- Verify the required OAuth scopes were granted. A successful account lookup or
   catalog sync does not prove `workflow` permission; current code checks GitHub's
   `X-OAuth-Scopes` before optional workflow writes. Do not log the token
  or an unredacted HTTP trace to check scopes.
- For a new seed, verify platform source exists, not only `LICENSE`; no active
   workflows should be included by default. For runner mode, review/merge the
   optional workflow PR and keep the selected branch protected.
- Verify container processing succeeds without runner access, including pricing,
   static use-case validation and configured quota reads. For runner capacity,
   configure the separate read-only Azure federation first.
- Sign in as a normal assigned employee: confirm portal role `guest`, no Setup or
  administrative workflow controls, and no GitHub sign-in requirement to consume
   published use cases. Verify that the same guest can submit a valid template ZIP
   for maintainer review without receiving administrator or repository permissions.
- Record ownership, token-expiry/reconnection procedures and a replacement operator
  before the authorizing GitHub user leaves the organization.

Official references:
- [Create an OAuth App](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
- [Device flow and token expiration](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow)
- [OAuth scopes and granted permissions](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [GitHub Apps versus OAuth Apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)

#### Connection troubleshooting and lifecycle

- If **Connect GitHub** cannot start device sign-in, open the same OAuth App under
   GitHub account/organization **Settings > Developer settings > OAuth Apps**.
   Check **Enable Device Flow**, save the change, and compare its public **Client ID**
   with the value saved in portal Setup. Do not enter the application name or client
   secret. Retry **Connect GitHub**; changing this GitHub checkbox needs no portal
   redeployment. A redirect URI is not used by this device flow, so changing the
   Entra callback cannot fix it. Current code reports known OAuth configuration
   errors separately from HTTP 429 rate limits, GitHub service failures, and
   host timeout/connectivity problems. For connectivity failures, check outbound
   HTTPS/DNS and organization proxy trust for `github.com`; do not bypass TLS validation.
   Older deployed images show only **GitHub device sign-in could not start** for
   HTTP failures; deploying the updated image is required for the detailed messages.
- If authorization succeeds but the portal reports **Could not verify the GitHub
   account**, older images reject Enterprise Managed User names containing an
   underscore, such as `username_enterprise`. Current code accepts these account
   and repository-owner names; rebuild and deploy the corrected organization image,
   then reopen Setup and connect again. No username change or new OAuth App is
   needed. **Check sign-in** now shows progress, prevents overlapping checks and
   times out a stalled poll after 45 seconds so it can be retried. A retry repeats
   account verification with the authorized token while the pending flow remains
   alive; it does not repeat consent. If the host restarted or the code expired,
   cancel the old sign-in and start a new connection. If token storage succeeded
   before a restart, reopening Setup can reload the saved connection instead.
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
   access/refresh pair is saved as one opaque protected credential value, including
   its expiry and account/client binding. It is also used in process memory after
   successful persistence. No token is stored in the application database or
   returned to the browser; SQLite contains only non-secret connection metadata.
- Persisting the connection requires host app-settings write access and can restart
   the host. Reopen Setup if that interrupts polling; no callback URL or manual
   token copy is required. Failures keep pending authorization in memory only for
   the device-code lifetime so it can retry without exchanging the same code again.
- Access-token expiry is respected by intake and sync. With a usable refresh token,
   the portal renews before GitHub operations when access expires within ten minutes,
   or on the next operation after idle time. The existing catalog scheduler also
   uses this path; it requires no new runner or user sign-in. Renewal is serialized
   and verifies the same account, current repository write access and branch before
   saving the rotated pair in the selected protected store. Storage can restart
   App Service just as connection setup does. F1 can pause the scheduler while idle;
   renewal resumes on use, provided the refresh credential is still valid.
- GitHub App user tokens typically last eight hours and refresh tokens six months.
   Successful renewal rotates both. Revocation, changed permissions, expired refresh
   tokens, or lost rotation responses can still require administrator reconnection.
   If verification or persistence fails after a successful exchange, the process
   keeps the new pair in memory and retries verification/storage after a cooldown,
   without exchanging the consumed refresh token again. A process loss before the
   pair is durably saved may require reconnecting; no permanent credential guarantee
   is implied. Never remove expiry metadata to bypass GitHub's token lifetime.
- Long-lived tokens can also be revoked by the user or organization. Disabling a
   repository stops its sync/publishing but does
   not revoke or erase the stored credential. Revoke consent in GitHub when access
   is no longer needed. Same-account renewal verifies existing repository access
   and waits for active catalog sync before replacing the credential. Disable the
   active repository connection before connecting another account.
- Preconfigured host tokens remain supported for existing installations, but the
   normal onboarding screen uses device sign-in. GitHub being disabled requires
   no credential. Secrets for Entra/OIDC authentication are separate and unchanged.
- Repository source copying is available only from a built organization export, which
   embeds an allowlisted, hashed source bundle. Connecting alone does not copy files.
   Explicit copying to an existing private repository is limited to empty or matching
   LICENSE-only contents, or verification of an already-complete identical copy.
   Source files are uploaded in one Git tree request and one source commit, not one
   request per file. The selected branch is checked again before a non-forced update
   and read back afterward; no source-copy success is reported before verification.
   If a remote operation partially succeeds, inspect the private repository before
   retrying. The application does not delete partially created repositories.
- Optional workflow PRs require a token whose GitHub `X-OAuth-Scopes` response
   header includes `workflow`. Normal source copies omit workflow files and work
   with repository permissions. Existing `repo`/`read:org` authorizations support
   container processing and catalog sync; updating the image does not upgrade scopes.
   If permission is missing, reconnect the same account through the organization
   OAuth device flow with `workflow` permission. The repository selection stays
   unchanged after verification. Do not paste an access token into the portal.
- If a failed or interrupted setup created a repository containing only `LICENSE`, keep
   it. Do not create a differently named repository. Reload Setup, choose **Copy
   portal source to an empty or LICENSE-only repository**, select the same repository,
   and confirm **Copy portal source**. The copy uses the source bundle in the deployed
   image and verifies current contents before any branch update. A retry after a
   completed copy only verifies the matching contents. If other code is present or
   branch rules block direct commits, use a reviewed history-free source import
   through the organization's normal process; do not bypass protection or overwrite
   changes. **Create workflow PR** installs automation only, not the full source.
   Entra and subscription Setup do not need resetting.
- Seeding failures report the operation and HTTP status with safe guidance; raw
   GitHub response text and tokens are not shown. A timeout may have completed
   remotely: inspect the branch before retrying any write.
- **Repository name already exists:** choose **Connect an existing repository** and select
   that repository. Changing the spelling creates a different repository; it does
   not repair the original. Connecting does not copy files. For an empty or matching
   license-only private repository, use the explicit source-copy action above.
- **Empty/non-JSON Setup response:** the app, authentication layer or gateway may
   have interrupted the response. The request may already have created the repository.
   Reopen the portal root and sign in if requested, reload Setup, and inspect the
   selected account/repository before retrying. Check App Service startup/restart
   logs for the request time. Do not create another repository as recovery. Current
   clients show the HTTP status and recovery guidance instead of a JSON parser error;
   older images can show "Unexpected end of JSON input".
- Uploaded use cases create review branches only while the configured GitHub
   connection has usable authorization. Otherwise the upload is retained in
   **Admin > Submissions**, not queued for later GitHub delivery. The response
   says it was saved in the portal only. A failed branch request is reported as
   unconfirmed because a lost response may still have created the remote branch.
   Before retrying, inspect GitHub and renew the connection in **Setup > GitHub**
   if needed. Reconnection and catalog sync do not automatically resend pending
   uploads. An administrator can download the saved ZIP and submit its workload
   folder through the organization's normal Git review process; no re-upload is
   needed for that recovery. Re-uploading creates another submission and may add
   a name suffix. The UI's submission name is not proof of publication.
   Before any GitHub write, the portal validates the proposed source with the
   sandboxed checks for the uploaded format(s), as used by catalog processing.
   Bicep-only and Terraform-only submissions do not require a counterpart,
   an AI service, GitHub Copilot, or GitHub Actions. It combines
   the upload with dependencies from a pinned target-branch tree, without pricing
   or quota calls. Missing dependencies, invalid source, unavailable isolation,
   validation timeout, or target-branch drift keep the ZIP staged and create no
   review branch. The upload response and Admin submission detail show the failure;
   correct the ZIP or repository dependencies before uploading again. This check
   requires the container validation tools even when scheduled processing uses runners.
   It is not a required GitHub status check: maintainers must revalidate later PR
   edits and direct repository changes before merge.
   Merge reviewed changes into the configured catalog branch. Protect that branch:
   it supplies executable infrastructure inputs to the managed-identity executor.
- Opening or refreshing the portal requests a background repository check for any
   signed-in user. Returning to the tab also checks when the last page check was
   at least 30 seconds ago. The visible sync status updates, and completed checks
   reload the cards and filter options without losing the search or selections.
   Concurrent requests share work; a ten-second cooldown after completed work
   limits repeated checks, with the browser retrying automatically. Requests
   during processing coalesce into one follow-up commit check. The five-minute
   background check remains as a fallback; **Sync catalog now** forces processing.
   A sync pins all reads to one commit. On failure the previous catalog stays active.
   No webhook endpoint, Azure CI credential or automatic app-code deployment is
   installed. Organization exports include an optional workflow generator, not active workflows; app
   source changes still require rebuilding and redeploying the image.
- Disabling GitHub stops sync and publishing; the last synchronized catalog remains
   available. Immutable runtime versions are retained for deployment consistency
   and must be included in organization retention/backup procedures.

Reference: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow

Repository input layout (all workload code is organization-authored):

```text
catalog/<slug>/metadata.json
catalog/<slug>/main.bicep
catalog/<slug>/parameters/dev.bicepparam
catalog/<slug>/parameters/prod.bicepparam
catalog/<slug>/pricing.json
catalog/<slug>/compiled/dev.json
catalog/<slug>/compiled/prod.json
catalog/<slug>/terraform/main.tf
catalog/<slug>/terraform/<other module files>
```

Metadata appears in the app even without runtime code, but deployment is disabled
until validated dev compiled ARM input or a Terraform main module exists. Container
processing compiles reviewed Bicep locally; runner mode imports generated inputs.
Neither path runs repository scripts during container processing. Generated pricing
is used only while its metadata hash matches.
The compiled file format is the same `template`/`parameters` object described
below. Slugs are lowercase letters/digits/hyphens, up to 80 characters. Nested
Terraform directories are not currently synchronized. Source symlinks and
submodules are not imported. Sync is bounded to 500 files, 150 directories,
2 MiB per file and 16 MiB total. Those limits are intentional.

#### Catalog processing

**Portal container (default and fallback).** No hosted runner, self-hosted runner,
GitHub workflow, or GitHub-to-Azure federation is required. The portal reads the
configured reviewed branch at a fixed commit, compiles Bicep, parses Terraform
syntax, refreshes public retail prices and reads configured Compute quota usage
with its managed identity. Negotiated pricing remains the existing on-demand
subscription pricing path; negotiated rates are not published to GitHub.

Configure deployment subscriptions under **Setup > Subscriptions**. The container
discovers all physical regions returned by Azure for each saved subscription;
previously saved manual region lists no longer restrict that discovery. With no
saved subscriptions, quota collection is skipped. Initial subscription setup still
does not perform quota checks. Inaccessible regional quota reads produce empty,
unknown results, never unlimited capacity. Capacity means quota headroom, not
guaranteed physical allocation or all-service quotas.

**Automatic regional refresh:** the container checks for due work at startup and
every five minutes, refreshing prices and supported capacity signals across all
physical regions of every saved subscription. Completed rows are saved to SQLite
as each batch finishes. A completed refresh is due again after 24 hours; changes
to workload cost/quota/default parameters or subscription pricing preferences
trigger a new refresh. Interrupted work resumes using still-current rows instead
of requiring a manual scan. No quota requests run in the initial subscription-save
request. Authentication and published workloads are required, but a container
schedule does not require GitHub connectivity or runners for already-published data.

Open **Capacity** to view these scheduled results for a workload and one or all
saved subscriptions. The screen loads results automatically and refreshes its
view every 15 seconds, without launching a scan. Each result has a check timestamp;
results older than 24 hours are marked stale. Failed checks remain unknown, with
prior successful results retained when available. Scheduler errors and missing
subscription/region access are shown rather than labeled healthy.

Results show monthly estimates and the actual retail/negotiated pricing source,
explicit negotiated-price fallback, regional Compute quota counters, and supported
workload-specific checks. Metadata `quota.requirements` drives service checks:
resource-provider locations indicate service offering; declared OpenAI model,
version and SKU use reported TPM capacity and model lifecycle. Missing metadata,
unsupported services, denied reads and partial data remain Unknown. Model/SKU
overrides must include an exact version for reliable checks. VM family/SKU-specific
allocation and App Service deployment preflight are not certified by these GET-only
checks. Offered/headroom is not a guarantee that a workload can deploy.

Pricing is evaluated in each region from the workload's component cost model.
Without one, pricing is Unavailable rather than zero. Negotiated prices require
exact regional meter IDs and supported billing access; fallback to retail is
visible per row. Price and capacity failures are independent, so a pricing failure
does not discard successful capacity results. Nothing is automatically deployed,
reserved or granted permissions by the comparison.

The container schedule runs three regional checks at a time, sharing a six-check
limit with interactive selection checks. A pass has a 20-minute budget; completed
batches survive timeouts and restarts. Azure metadata and retail meters have a
five-minute in-process cache. Search, sort and CSV export cover all matching
scheduled results; the table displays up to 250 rows at once. Regional snapshots
and refresh job state are part of the SQLite backup scope.

**At deployment time, in both processing modes:** selecting a subscription, region
or parameter automatically refreshes its price and capacity display. There is no
separate quota-check button. The server performs the same check again before
creating a deployment record or starting provisioning, bypassing cached price and
capacity reads. A known unavailable capacity signal blocks the request. Incomplete
pricing, unsupported checks or unknown capacity remain advisory and visible; they
are not interpreted as guaranteed availability. The selected check summary and
timestamp are retained with the deployment. This does not reserve capacity, and
Azure may still reject allocation or policy validation later.

**Runner mode:** the scheduled capacity workflow runs the same regional-check
implementation and publishes its results with the reviewed capacity artifact.
The portal imports those results; it does not duplicate a scheduled scan while
the selected runners are available. Runner results contain public retail prices
only, never negotiated billing data. For subscriptions configured for negotiated
prices the screen labels that retail limitation; live selection and deployment
checks still use the saved pricing preference. Unavailable/unverified runners
activate the container schedule automatically. Existing repositories must review
and merge the updated workflow/helper PR, enable the capacity schedule and grant
the runner identity read access to the intended subscriptions; none of these
permissions or repository changes are applied automatically.

The compiler subprocess receives no portal/cloud credentials, cannot make network
connections, and cannot read the portal database, application source or other
process environments. It can read its temporary input directory and required
runtime libraries/system metadata. Limits include 60 seconds per process, 30 CPU
seconds, 8 MiB per output file, bounded runtime heaps and an 8 GiB virtual-address
ceiling. Linux x64 Landlock/seccomp support is mandatory; unavailable isolation is
a validation failure, never permission to run unrestricted. Validate compatibility
on the chosen hosting kernel before production use.

Bicep module dependencies must be supplied as local files under `catalog/` or
`modules/`. Compilation uses `--no-restore`; registry modules/extensions are not
downloaded by the credential-free validator. Vendor approved dependencies or
supply reviewed precompiled ARM wrappers. Input depth is limited to eight levels,
with 500 files and 16 MiB total. Terraform checks syntax with `fmt -write=false`;
it does not initialize providers, execute provisioners, or certify provider schemas.
ARM wrappers receive structural checks. Deployment permissions, resource policy,
provider semantics and allocation feasibility remain deployment-time checks.

If Operations reports **BCP091**, Bicep could not read a referenced file. Check the
reported template and commit all required local modules to the configured branch
at their referenced relative paths, including transitive dependencies. A workload
folder alone is incomplete when it references a repository-level `modules/`
folder. Review and merge the complete source, then refresh the portal. Compiler
error codes and the input path are shown; raw compiler output and parameter
values are withheld. Validation failures keep the last good catalog active.

Processing writes only local versioned runtime artifacts and database snapshots;
it does not commit generated files to GitHub. Job state is persisted in SQLite.
Interrupted jobs are retried after restart; failures retain the last good catalog
and capacity together. Page loads and tab returns request a commit check without
waiting for the five-minute background interval. Validation and processing must
finish before new use cases become available; failures remain visible and do not
publish partially processed inputs. A page check does not force a rebuild of an
unchanged commit. New commits are also checked every five minutes; unchanged
catalogs/prices/quotas refresh at least daily while the app is running. Manual
refresh forces a new processing pass. One worker/instance remains required.
**F1 remains supported**, but idle unload/restarts delay processing and schedules;
this is not a guarantee of uninterrupted background service.

**Runner choices.** Setup probes the connected repository automatically, and
**Check runner access** repeats the check. It reads Actions policy, visible online
Linux x64 self-hosted runners and recent job assignments. A hosted option requires
a recent successful `ubuntu-latest` job; a failed job with no assigned runner
blocks the hosted option. An Actions-enabled flag alone is insufficient.
Missing read permissions, timeouts or insufficient evidence are **unverified**,
not proof that runners do not exist. Only verified choices are selectable; the
container remains available. Probes do not create workflows, dispatch test jobs,
grant administrator scopes or modify enterprise policy. Newly enabled runner
access may need an administrator's successful test run before it can be detected.

For self-hosted runners, provision and maintain a dedicated approved Linux x64
environment with Git, Python 3.12, Azure CLI, Bicep 0.38.33 and Terraform 1.9.8;
install/runtime dependencies must be reachable under organization policy. Restrict
runner access to trusted repositories, use fresh isolated environments, and keep
it separate from the portal host and its credentials. Busy online runners remain
eligible to queue work. Labels are verified against repository-visible candidates.
Do not reuse a persistent runner that executes untrusted PRs for privileged jobs.

Saved runner eligibility is rechecked periodically. If runners become unavailable
or cannot be verified, **pricing, capacity and use-case static validation fall back
to the portal container**, with a visible reason. No paid tier or role upgrade is
performed. Existing remote workflows are not automatically disabled or deleted;
when migrating from an earlier release, disable unwanted workflows through the
repository's approved process to stop their independent schedules.

GitHub-hosted runners are not available for repositories owned by Enterprise
Managed Users, even when Actions is enabled. Organization-owned repositories can
have different permissions. Repository policies and runner access must be checked
for the selected repository, not inferred from another organization's workflows.

#### Repository workflows

Workflows are optional and are not installed in new exports or source copies.
After selecting verified runners, **Create workflow PR** proposes two newly
generated organization-owned workflows. No original publisher workflow, identity
or credential is copied.

| Workflow | Triggers | Output |
| --- | --- | --- |
| Validate catalog and refresh retail prices | Reviewed branch pushes, daily at 05:17 UTC, manual | Compiled ARM wrappers, Terraform syntax checks and public-retail pricing |
| Refresh subscription capacity | Daily at 06:37 UTC; disabled until explicitly enabled | Subscription/region quota and workload regional retail-price/capacity snapshots |
| Refresh Azure roadmap and retirements | Daily at 06:47 UTC | Public Azure Updates lifecycle snapshot; no Azure credentials or capacity opt-in required |

**Existing installation:** update the portal image once to receive this feature,
then choose verified processing runners and **Setup > GitHub > Create workflow PR** for the already-connected repo.
Review the workflow and helper changes in GitHub and merge them yourself. This
does not rerun Setup, replace your configuration, deploy the app or grant Azure
permissions. Repeating the action reopens the same pending PR. Inspect a partial
`portal/workflows-*` branch if PR creation fails; the portal never deletes it.

Older OAuth authorizations may lack `workflow`. They can still sync catalogs.
For workflow PR permission errors, reconnect the same account with the updated
scopes while keeping the selected repository, or
use your own GitHub credentials to install the files locally from a current export:

```powershell
python tools/portal_workflows.py --target C:\repos\company-portal
```

The local installer makes no remote writes and refuses differing existing files;
review/merge those files manually. Commit and push the reviewed files yourself.
The target is an existing portal-source clone with its shared pricing module.
Protect the catalog branch and restrict workflow changes to trusted reviewers.

Workflows use the repository's default branch unless the repository variable
`PORTAL_CATALOG_BRANCH` names the portal's configured sync branch. Keep the
workflow definitions on the default branch for schedules/manual discovery, and
the workflows/helpers on the selected catalog branch. These workflows do not run
untrusted pull requests. Successful runs publish allowlisted generated
files through a separate job with `contents: write`. Review your organization's
Actions policies and permissions before enabling publication. Branch protection
is never bypassed: if bot pushes are blocked, use the retained generated artifact
to propose the output through your approved PR process. An advanced branch causes
publication to fail safely; rerun the workflow against the new head. No force push
or automatic merge is used. Artifact retention follows the repository policy.

Catalog builds use pinned Bicep tooling and the portal's shared retail pricing
engine. Bicep workloads use `main.bicep` and optional `parameters/dev.bicepparam`
and `prod.bicepparam`; parameter files must reference that workload's main file.
Without dev parameters the compiled wrapper uses template defaults. No prod
wrapper is generated without prod parameters. Secure parameter literals are
rejected; do not commit secrets. Terraform files remain organization-authored and
are parsed using `terraform fmt -write=false`; providers and provisioners are not
executed by this validation step. Incomplete price lookups fail the build and retain previous outputs.
Review authored metadata and cost components, then wait for the workflow to finish
before syncing. Publishing generated inputs is not a workload deployment.

To enable runner-based scheduled capacity, an organization administrator configures:

1. An organization-owned Entra application or managed identity with a GitHub OIDC
   federated credential for the `portal-capacity` environment in this repository
   (default subject `repo:OWNER/REPO:environment:portal-capacity`; account for any
   organization-customized subject). The audience is `api://AzureADTokenExchange`.
   Grant Reader only on the subscriptions to be queried. Do not reuse the portal's
   broader deployment roles just to collect quota.
2. A GitHub environment named `portal-capacity`, restricted to the reviewed
   workflow branch, with required reviewers as dictated by company policy. Add
   environment variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `PORTAL_SUBSCRIPTION_IDS` (space-separated GUIDs).
   These are identifiers, not passwords; no client secret is required.
3. Repository variable `PORTAL_CAPACITY_ENABLED=true`, after reviewing the above
   access. The collector discovers all physical regions for those subscriptions
   (up to 200 subscriptions); the old 100-combination limit does not apply. Existing
   manually invoked helpers may still pass an explicit region list, but generated
   workflows use automatic discovery. Job timeouts and response-size limits remain.
   Run **Refresh subscription capacity** manually and verify success first.

GitHub federation is separate from portal sign-in and is not provisioned by Setup.
It is optional and never blocks initial installation. The collector performs only
subscription and Compute usage GET requests; it creates no resources or roles.
All subscriptions must be enabled in the configured tenant. Capacity data is
committed to the organization repository, so consider its audience and retention.
Only public retail rates are committed; negotiated rates remain runtime-only.

On the next successful sync, the existing image consumes generated pricing and
the capacity snapshot without a rebuild. **Capacity** displays configured
subscriptions only, with region selectors, quota counters and the snapshot time.
This is Compute quota headroom, not physical allocation or all-service availability.
Other service/SKU signals remain unknown. Invalid feeds preserve the last good
catalog and snapshot. Removing a workload directory removes it from the catalog;
it does not delete deployed Azure resources. Keep persistent storage and protected
host settings when updating the image: ordinary edits/updates need no onboarding.

### 3. Subscriptions and pricing

For an individual subscription:

1. Enter the nonzero **Subscription ID** GUID from Azure, not its display name or
   full `/subscriptions/...` resource path. Select the pricing preference.
2. Select **Add and save**, or press Enter. Azure verifies access and tenant
   membership, then saves the subscription. Its resolved name and ID appear in
   the list with the updated saved count. There is no second save button.
3. Change a saved subscription's pricing directly in its row. Changes save
   automatically after verification. A failed save restores the previous pricing;
   a failed addition keeps the entered ID so you can correct it and retry.

For bulk additions, expand **Import CSV**, select a UTF-8 CSV and choose
**Import and save**. Selecting a file alone does not save anything. Matching IDs
update pricing; other saved subscriptions are retained. The whole list is verified
before saving, and a failure leaves the saved list unchanged. The header is:

```text
subscriptionId
```

The only required column is `subscriptionId`. An optional `pricingMode` column
accepts `retail` (the default) or `negotiated`. Do not supply names, tenant IDs or
regions: Azure returns each subscription's name and tenant using the application's
managed identity. The browser user's sign-in alone does not grant that identity
Azure access. Names and tenant membership are resolved for both individual and
CSV additions and rechecked before saving; browser-supplied metadata is rejected.

Subscription field-validation failures show a field name and corrective guidance,
without displaying raw request values. For further diagnosis, inspect the failed
`PUT /api/admin/setup/subscriptions` response in browser developer tools. The request
contains a `subscriptions` list with only `subscriptionId` and `pricingMode` per
entry. Managed-identity access failures report Reader-access guidance separately.
Older images may still show the two-step workflow and generic validation messages;
deploy the updated image to receive these UI changes. Updating the image does not
grant roles or alter existing subscriptions.

Quoted fields and UTF-8 BOMs are supported. Imports are limited to 128 KiB and
200 subscriptions. Review the saved rows after **Add and save** or **Import and save**.
Every subscription must be enabled, accessible to the managed identity, and owned
by the configured Azure tenant. A failed lookup does not save a partial list.
Saving verifies read access and tenant ownership, not every workload's deployment
permissions. Existing subscriptions cannot be removed in this version, to avoid
breaking cleanup of tracked deployments.

Deployment and estimate requests select from this persisted allowlist, which both
ARM and Terraform executors enforce. Adding a subscription does not grant RBAC.
Grant required deployment roles in each organization subscription separately.

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

### 4. Branding

An administrator can choose **Setup > 4. Branding** during initial setup or later.
Enter a **Site name** (1-80 characters) and **Footer disclaimer** (up to 500
characters), then select **Save branding**. Leave the footer blank to hide it.
The header, browser title and footer update immediately; other users see the
saved branding on their next page load. Guests can view it but cannot change it.

Branding is plain text, including Unicode. HTML is displayed literally, never
executed. Do not include secrets or private information in visible branding.
Review your organization's disclaimer wording with the appropriate owner. The
default footer reminds users to verify indicative cost estimates against their
cloud agreement. Customizing that footer does not change the MIT license,
copyright notice or obligations included with the source.

For deployment-time defaults, set these optional host environment values:

| Setting | Default | Purpose |
| --- | --- | --- |
| `GALLERY_BRAND_NAME` | `Deployment Portal` | Site name before branding has been saved in Setup |
| `GALLERY_FOOTER_TEXT` | `Cost estimates are indicative; verify against your cloud agreement.` | Default footer notice; an explicit empty value hides it |

Saved Branding settings take precedence over these environment defaults. They
live in the persistent installation database and survive ordinary image upgrades.
A deliberate application reset removes saved branding with other setup data;
host environment defaults remain. No rebuild, Entra registration change, GitHub
reconnection or additional consent is needed to save branding. Azure resource
names, repository names and permission boundaries are not renamed by branding.

#### Neutral tool names and existing installations

The operations guide is now `docs/OPERATIONS.md` in the development checkout and
remains `README.md` in exported source. The tools are now
`tools/build_distribution.py`, `tools/portal_source.py`,
`tools/portal_workflows.py` and `tools/preview_portal.py`. Update local commands
and automation to these paths when adopting this source version. Review the
source changes in existing repositories; image deployment does not rename their
files automatically, and the workflow PR action updates automation, not the whole
platform source. Old generated archives are not rewritten in place.

New exports use `GALLERY_DISTRIBUTION=organization` and an `organization`
distribution marker. Existing `GALLERY_DISTRIBUTION=customer` values and old
markers remain supported; changing these is not required for an upgrade. The
legacy API field `customerOperated` and internal compatibility names are retained
to avoid breaking existing clients. They are not displayed as product branding.

## Publish your own workloads

**ZIP uploads support Bicep, Terraform, or both.** Include `main.bicep` with
`parameters/<env>.bicepparam`, or `terraform/main.tf` with its `.tf`, `.tfvars`
and JSON companion files. A Terraform-only bundle may put `main.tf` and its
companions at the root; upload normalizes these into `terraform/`. A single
wrapper directory is accepted. Exclude secrets in variable files, Terraform
state, saved plans and `.terraform` provider caches. Before creating a review
branch, the portal compiles supplied Bicep and syntax-checks supplied Terraform
in its isolated compiler sandbox; it never deploys resources during upload.
The files remain staged or on a review branch until approved. The flat
Terraform-directory sync limitation and runtime CLI prerequisite still apply.

**Supporting documents can accompany the infrastructure.** Word (`.docx`), Excel
(`.xlsx`), PowerPoint (`.pptx`), PDF, CSV and TSV files are accepted at the workload
root or in subfolders such as `docs/`. They are preserved byte-for-byte in staging,
the administrator's ZIP download, and the GitHub review branch. Catalog sync does
not import them into deployment artifacts, and compilers do not receive them.
The portal does not render, convert or execute these documents.

**PowerShell helpers (`.ps1`) are review-only attachments too.** Include them at
the workload root or in folders such as `scripts/` or `runbooks/`. They are kept
unchanged in the review branch and ZIP download as ordinary files, excluded from
compiler inputs and deployment artifacts, and never run by the portal. They are
not supported as automatic pre/post-deployment hooks or as runtime files for
Terraform provisioners. An administrator must review them before an operator
chooses to run them separately. Do not include credentials in scripts.

The ZIP remains limited to 5 MiB compressed, 25 MiB expanded, 300 files, and
5 MiB per file. Infrastructure compiler inputs retain their separate limits of
2 MiB per file and 16 MiB total, excluding review-only attachments. Office attachments
must be non-encrypted modern packages, with at most 2,000 internal entries and
25 MiB internally expanded content; legacy `.doc`, `.xls`, `.ppt`, macro-enabled
formats, binary executables and other script extensions are not supported.
CSV/TSV and `.ps1` files must be UTF-8 or BOM-marked UTF-16.
Basic format checks are not malware scanning: administrators must review and scan
attachments before opening them, including PDF active content and spreadsheet
formulas or external links. No attachment is automatically trusted or published.

**The default is native-format processing with no AI dependency.** No conversion,
AI configuration, or GitHub AI access is needed. A missing counterpart is neither
generated nor required. Once an administrator reviews and merges the workload,
catalog sync advertises only the available deployment engine(s); the deployment
API rejects an engine that the workload does not provide. Bicep-only workloads
compile to ARM deployment inputs, not Terraform. Terraform-only workloads retain
their Terraform source and do not receive a placeholder Bicep template.

If both formats are included, both must pass their static checks. Missing local
modules or invalid source in a supplied format are still errors: include those
dependencies in the upload at its referenced paths or in the configured repository.
Static checks do not prove deployment feasibility or equivalence between formats.
There is no automatic merge, publication, or deployment of a submission.

Author and review your infrastructure code in an organization-owned repository. The
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
- For connected repositories, the organization workflows above produce retail pricing
   and Compute quota snapshots for runtime sync. The lifecycle feed is refreshed
   separately as described below. Other region/SKU registry feeds are not supplied;
   their snapshots remain empty unless separately implemented.

Redeploy your organization-built app artifact with those inputs. These are organization
additions, not templates supplied by this distribution. Uploads in the UI are
staged for administrator review, **not** automatically installed or executed.
GitHub publishing is off unless both a token and an organization-owned `owner/repo`
are explicitly configured. Do not provide a publisher-owned repository or token.

Only publish trusted code. Terraform providers, provisioners, and uploaded
deployment artifacts are executable infrastructure code with the host identity's
permissions; this is not a sandbox for untrusted workloads.

## Roadmap and retirements

**Roadmap & retirements** is populated automatically from Microsoft's public
[Azure Updates feed](https://azure.microsoft.com/updates). It is a global service
lifecycle feed, not a scan of resources deployed in your subscriptions. It requires
outbound HTTPS to `www.microsoft.com`, but no Azure role, subscription credential,
GitHub token or manual refresh in container mode. The collector requests only
notice IDs, titles, products, statuses and availability dates from the public
Microsoft Release Communications API; it sends no portal or subscription data.

- **Container:** starts after normal application startup, including on an empty
   catalog. Refresh is due 24 hours after the last successful fetch. The worker
   checks every five minutes; a failed fetch is retried after ten minutes. Restarting
   does not reset a successful feed's age. F1 idle unloading can delay all in-app
   schedules, so the view shows stale data instead of implying continuous service.
- **Runners:** the opt-in `portal-lifecycle.yml` workflow runs daily at 06:47 UTC,
   using the same collector. It needs no Azure login or quota-workflow enablement.
   Review and merge the updated workflow/helper PR in existing repositories, and
   ensure GitHub Actions scheduling is enabled. GitHub schedules run from the
   default branch; checkout and publication still target the configured reviewed
   branch. GitHub may delay scheduled jobs. The app imports the validated feed on
   catalog sync; unavailable or unverified runners fall back to container refresh.

The collector fetches complete bounded pagination (up to 200 pages, 20,000 records
and 16 MiB of metadata), never silently publishing a partial feed. Retirement
notices with known past months are excluded; notices with unannounced dates remain.
Preview and GA notices with known dates cover the past 12 months. Each service
category shows up to 50 notices, deduplicated by ID/title. The source links carry
the exact notice ID and always point to Azure Updates. Open the source notice to
confirm applicable versions, exact retirement dates and migration instructions.

The roadmap view loads current status when opened: last successful refresh,
container/runner source, next planned run or retry, and failure/staleness messages.
**Admin > Operations** also shows the roadmap refresh status. Workload notices
match service names in cost components and the workload service list; this is a
service-level association, not proof that a particular resource version is affected.
Unrecognized services still appear under their feed product names on the full
roadmap. No matching notices must not be interpreted as a guarantee of no retirements.

Successful snapshots and job status are retained in SQLite under `lifecycle_snapshot`
and `lifecycle_job`, and belong in database backups. A timeout, malformed response,
unsafe pagination link, empty upstream feed or invalid runner artifact leaves the
last successful snapshot intact. The screen marks it stale after 24 hours. Before
the first success it reports that no successful refresh exists, not that there
are zero retirements. Local previews do not run the background collector.

## Data and network boundaries

Identity claims, roles, deployment records, audit events and staged uploads are
stored on organization-controlled storage. Deployment metadata includes user object
IDs, names and UPNs, so the installation does process personal data. Optional UI
usage-event collection is disabled in organization mode. The operator must configure
retention, deletion, backup, access control and incident procedures.

There is no publisher-operated telemetry endpoint, remote support channel, or
publisher credential in the package. Do not configure publisher-controlled
monitoring, repositories or identities. Restrict operational access to your staff.

Necessary service traffic still exists:

| Destination | Purpose |
| --- | --- |
| Organization Entra tenant and App Service Authentication | Sign-in and session handling |
| Azure management APIs | Organization-authorized resource creation and deletion |
| Microsoft Graph, during confirmed Entra setup or optional Reader grants | Create organization app registration/credentials or resolve principals; guest invitations remain disabled |
| Organization OIDC provider, when configured | Organization login and claims through App Service Authentication |
| Azure Consumption Price Sheet API, when negotiated pricing is selected | Read organization subscription rates; retail fallback if unavailable |
| Azure Retail Prices API | Cost queries using region and service selections |
| Microsoft Release Communications public Azure feed | Anonymous lifecycle metadata refresh; no subscription or identity data sent |
| Organization-configured GitHub repository, only if enabled | Uploaded code and review metadata |
| Package/provider registries | Organization-initiated dependency and provider installation |

Azure and Entra are Microsoft-operated services; Microsoft may process data as
their service provider under the organization's own agreements. This is distinct
from the platform publisher collecting application telemetry. Organization hosting
alone does not mean that no personal data is processed by any service provider.

## Database and backups

The portal uses a **SQLite database as its system of record**, not just a temporary
log. It records configuration, user access, activity and deployment lifecycle data
used by the admin reports and cleanup scheduler. The standard container stores it
at `/home/data/deployment-portal.db`. `GALLERY_DB_PATH` overrides that location;
verify the actual path before backing up or restoring an installation. Keep one
worker and one app instance, with persistent storage writable by UID/GID 10001.

### What SQLite stores

| Table | Information retained |
| --- | --- |
| `users` | Entra user object IDs, names, sign-in names (UPNs), admin/guest roles and first/last-seen timestamps |
| `deployments` | Requesting user, workload, environment, engine, subscription/resource group, parameter values, catalog version, status, error details, expiry and cleanup policy |
| `events` | Timestamped activity and audit events, such as setup changes, submissions and deployment outcomes, with associated user/resource metadata; optional UI usage events are disabled in organization mode |
| `submissions` | Submitter, upload metadata and file list, staged-file path, review status, original GitHub review branch and comparison URL |
| `setup_settings` | Branding, subscriptions, repository/account settings, authentication and credential-storage metadata, catalog/pricing/quota and lifecycle snapshots, repository-scoped catalog removals, refresh jobs and optional installer state |

GitHub access tokens and Entra client-secret values are stored separately in
protected App Service settings or Key Vault, not as integration credentials in
SQLite. However, the database contains personal information, resource identifiers
and submitted deployment parameter values. Treat it and its backups as sensitive.
Compiled catalog artifacts, uploaded code and Terraform working/state files are
also stored separately; backing up the database alone does not preserve them.

### Backup responsibility

**Persistent storage is not a backup.** It retains data across ordinary restarts
and image updates, but does not protect against deletion, corruption or storage
loss. The persistence fix does not replace backups. Logging continues without a
backup, but lost records cannot be recovered unless another valid copy exists.
Restoring an older backup restores only the history present at that backup point.

The organization operating the portal must schedule and monitor backups, choose
retention and a frequency that meets its acceptable data-loss window (RPO), and
test restoration against its recovery-time objective (RTO). Back up before changes
that affect persistent data as well as on the regular schedule. Record backup
timestamps and restore-test results; alert on failed or overdue backups. Keep
encrypted, access-restricted copies outside the app's writable data directory and
in a separate failure domain under organization policy. Neither a GitHub source
copy, a Docker image nor an admin CSV export is a complete portal-state backup.

`Deploy-Portal.ps1` does **not** create, schedule or verify backups. Routine
`-DeployImage` updates omit the backup prompt for convenience, not because backups
are unnecessary. `-BackupConfirmed` is only an operator acknowledgement for
explicit `-MaintenanceReady` updates; it does not perform a backup.

### Backup and restore scope

1. Identify `GALLERY_DB_PATH` and `GALLERY_TF_WORK`, including any overrides.
   Preserve the SQLite database, its adjacent `submissions/` and `catalog-versions/`
   directories, and Terraform working/state data (normally `/home/data/tf-runs`).
   The complete `/home/data` directory covers these standard container paths;
   overridden paths outside it require separate coverage. Preserve protected host
   configuration and credential recovery procedures, including Key Vault when used,
   along with the compatible image digest. Keep secrets out of logs and Git.
2. Use a validated SQLite-consistent backup procedure coordinated with adjacent
   file changes, or stop the app and all other database/file writers before copying
   the complete data set. Do not copy the live database file alone. For stopped
   backups, retain any `-wal`, `-shm` and `-journal` companions together with the
   database; do not manually delete them. For older WAL-based installations, follow
   [the stopped-app WAL procedure](#upgrade-from-sqlite-wal), not a live SCM database read.
3. Test restoration on an isolated copy without live Azure deployment permissions.
   Check database integrity, setup/roles, reporting history, staged uploads, catalog
   versions and Terraform state, plus ownership and permissions for UID/GID 10001.
   A backup job reporting success alone is not proof of recoverability.
4. Before a production restore, stop all writers, retain the current data for
   investigation, and restore a consistent set with compatible application code
   and configuration. Reconcile deployment records with actual Azure resources
   before restarting the cleanup scheduler: a restored backup can contain stale
   statuses or already-expired leases. Restoring SQLite does not roll back Azure
   resources, GitHub commits or external credentials.

Losing or resetting the database loses admin reporting history and can lose the
portal's tracking of existing workloads and their cleanup deadlines. Those Azure
resources remain deployed and may continue incurring charges. Do not reset the
database as a substitute for restoring a backup or investigating missing records.

## Administrator operations and reporting

**Admin > Operations** separates current operational counts from activity in the
selected 7-, 30- or 90-day period. Failed deployments may still hold resources;
overdue cleanup and next-24-hour expiry counts exclude externally managed resource
groups. Summary counts cover all database records, not just the loaded tables.
Catalog health shows the selected repository, last successful sync and current
processing result. These are portal records, not live Azure resource inventory
or a billing report. Review failures and cleanup obligations against Azure.

Deployment and submission worklists support search, status filters, refresh and
CSV export. Deployment worklists load the latest 500 records; submissions load
the latest 200. Their counts and exports cover only the loaded, filtered rows,
and the UI marks a reached limit. Operations exports carry the report timestamp
and activity cutoff. Daily request buckets use UTC; boundary days are partial.
Exported identity and resource metadata must follow company data-handling policy.

**Admin > Use cases** lists the synced catalog with **Published**, **Removed** and
**All use cases** filters. The action selector offers **Remove from portal** and
**Remove from repository** (or **Restore to portal** for a hidden entry). Only
administrators can use these actions; confirmation and audit records are required.

**Remove from portal** hides
the use case and blocks new portal deployment requests, including requests from
an older open browser. It does not delete GitHub source, staged submissions,
versioned artifacts, existing deployment records or Azure resources. Existing
deployments and their cleanup schedules continue unchanged.

Removals are saved in SQLite separately from the synced source snapshot, scoped
to the source repository and use-case path. They survive page refresh, restart
and subsequent repository syncs. Reusing the same path in that repository remains
removed until an administrator selects **Restore to portal** from the Removed
filter. Restoring clears the removal; the use case appears only if it exists in
the latest successful catalog snapshot. A changed catalog version is rejected
with a refresh-and-retry message. Keep these records in the normal database backup.
This is site visibility management, not source deletion or an exemption from
source validation; remove or repair invalid source through repository review.

**Remove from repository** deletes exactly `catalog/<use-case>/` and every file
below it by committing directly to the configured catalog branch. The confirmation
names the repository, branch and folder and states that this is an immediate source
deletion, not a review-branch request. Only an administrator can confirm it. On
verified success, the use case is also removed from the saved portal catalog and
a sync is queued. **View verified commit** opens the resulting GitHub commit.
This action does not delete the branch itself or create/merge a pull request.

Shared `modules/`, sibling use cases, site files, staged submissions, existing
deployments and Azure resources are not deleted. Portal restore cannot restore
deleted source; use a reviewed Git revert to recover it. Removing files does not
erase Git history. Verified source deletion clears this entry's portal-removal
record; restoring reviewed source at the same path can publish it again on sync.

The backend verifies write access and pins the request to the displayed snapshot,
source commit, repository and branch. It enumerates only the selected folder,
with limits of 500 files, 150 directories and eight nested levels. Symlinks are
deleted as files, never followed; submodules and unverifiable trees require manual
review. GitHub's `createCommitOnBranch` mutation uses `expectedHeadOid`, atomically
rejecting a changed branch instead of overwriting concurrent work. GitHub branch
protection and repository rules remain enforced. If a pull request is required,
perform the deletion through the normal GitHub review process; the portal does
not weaken rules or force-update branches.

The resulting commit, parent, branch head and retained sibling hashes are checked
before local catalog changes are reported as successful. Missing source is reconciled
only after verifying the confirmed branch. Requests are limited to one per minute
with a 90-second timeout. A lost response may occur after GitHub committed the
deletion: inspect the configured branch and sync before retrying. No deletion is
automatically retried. Failures report a safe operation stage and HTTP status where
available without exposing tokens or raw remote responses. Old portal tabs lacking
the direct-deletion acknowledgement are rejected; refresh after upgrading.

Older images created deletion review branches instead. Those branches are not
automatically deleted or merged by this version. Review them separately before
making a new direct-deletion request.

**Delete submission** removes the staged files and, when present, its original
GitHub review branch. The confirmation names the branch. The backend checks the
recorded original repository, submission-specific branch name and write access,
and refuses default, protected or currently selected publication branches. A
GitHub failure leaves the submission and local files available for retry; a
verified already-absent branch is treated as deleted. Deleting a review branch
may close its open pull request; it does not undo merged code or deployments.
The database retains a Deleted record and audit event. Submissions recorded as
merged must be managed through the repository. Review-branch status is not a
live pull-request status; a later merge in GitHub is not automatically reconciled.

## Acceptance checklist

Complete these in an organization-controlled non-production environment before
publishing executable workloads for employees:

- [ ] The app is reachable under organization-selected network policy; no VPN or private endpoint is required by the solution.
- [ ] Image pulls and optional vault access work under organization policy; the scripts have not changed network restrictions.
- [ ] An authorized Entra administrator reviewed and granted tenant-wide consent for the actual sign-in scopes; the assigned initial administrator can sign in without **Need admin approval**, with **Assignment required = Yes** retained.
- [ ] Unauthenticated browsers enter company Entra sign-in; wrong-tenant and unassigned identities are rejected. Only approved employees/groups are assigned.
- [ ] The configured initial administrator reaches Setup without an installation code; an ordinary tenant member cannot access administrator setup, even if they visit first.
- [ ] The primary connection is visible in Authentication, and no second provider is required to continue.
- [ ] The company GitHub OAuth App authorizes successfully, writable repository selection works, and Sync catalog now publishes reviewed inputs when enabled.
- [ ] Subscription IDs resolve to Azure display names and the correct tenant; saving outside-tenant or inaccessible targets fails without partial updates.
- [ ] Retail or negotiated pricing reports its actual source and any fallback; no quota checks run during initial setup.
- [ ] A disposable reviewed workload deploys and cleans up with the host managed identity, including failed-cleanup recovery.
- [ ] A restart retains setup, users, catalog versions and deployment records on persistent storage.
- [ ] An administrator can remove and restore a use case without deleting source or active deployments; ordinary users cannot change visibility, and removed use cases stay unavailable after sync.
- [ ] Direct repository removal commits only the confirmed folder deletion, enforces branch rules and expected-head checks, preserves unrelated files/deployments, and reports uncertain outcomes without falsely claiming success.
- [ ] Scheduled database and adjacent-file backups meet the agreed RPO/retention policy; an isolated restore has recovered history, setup and workload state, and backup failures are monitored.
- [ ] Credential rotation, log/data retention, backups, restore testing and an operational owner are in place.

Local automated tests and UI previews do not establish live Entra or workload
deployment readiness. An accepted deployment or a successful
image push is not a substitute for these checks.

## Updates and recovery

1. Build and scan a new organization image and record its digest. Retain the previous
   release so a reviewed rollback is possible. Do not overwrite release tags.
2. Back up protected host settings, role assignments and persistent application
   state before upgrading. Use a consistent SQLite backup procedure or stop the
   application for the backup; copying the live database alone is not sufficient.
   Include catalog versions, uploads and Terraform state as applicable.
3. Deploy using your organization change process. Keep the same persistent database,
   organization-selected ingress, Entra registration and identity unless an intentional migration
   has been reviewed. No first-run installer is needed for routine upgrades.
4. Recheck sign-in, administrator access, catalog operations and cleanup. A
   rollback must account for database/schema compatibility as well as image versions.
5. Rotate Entra credentials before expiry and verify Key Vault reference versions
   if used. Reauthorize GitHub after expiry or revocation. Disabling GitHub does
   not erase or revoke its saved token; revoke it in GitHub when no longer required.
6. Monitor failed deployments/cleanup, persistent storage usage and access to
   logs, SCM and backups. Keep one worker and one instance. App source changes
   require rebuilding/redeploying; catalog sync does not deploy application code.

### Deploy local site changes

For an already-installed portal, use the same script with `-DeployImage` from the
organization's local source checkout. This is an image-only update, not Prepare
or first-run installation:

```powershell
./tools/Deploy-Portal.ps1 -DeployImage -WhatIf
./tools/Deploy-Portal.ps1 -DeployImage
```

The second command asks for confirmation before publication and before changing
the app. Routine updates do not ask for a backup acknowledgement or display a
WAL migration warning. It builds the checkout containing
the script (not another current working directory), pushes a new `linux/amd64`
image, verifies its published digest, stops the app, changes only
`linuxFxVersion`, verifies configuration readback, and starts the app. Expect
brief downtime. It preserves App Service settings, credentials, identity,
authentication, network settings, database/upload paths, and browser Setup.
It does not create Azure resources, grant roles, reconfigure Entra or call GitHub.
It does not run tests, scan images or certify database compatibility for you.

Target selection reuses the non-secret `.portal/prepare.json` from Prepare when
available. `-ConfigPath` selects a different saved configuration. A fresh clone
does not include that ignored local file; supply all four explicit target inputs
or use the prompts. Explicit inputs never merge with a saved target:

```powershell
./tools/Deploy-Portal.ps1 -DeployImage `
   -SubscriptionId '<hosting-subscription-guid>' `
   -ResourceGroupName '<existing-resource-group>' `
   -AppName '<existing-app-service>' `
   -RegistryName '<existing-registry>' -WhatIf
```

Requirements: PowerShell 7, Azure CLI signed into the hosting subscription, Docker
with Linux container support, App Service/plan/configuration read access,
App Service configuration/stop/start access, and ACR metadata/push access under
organization policy. No new Graph access is required for this update path;
existing Entra policy remains the operator's responsibility. Existing managed
identity image pulls, port 8000 and persistent storage must already be configured.
The registry and image repository must match the current deployment. F1 is retained.

- `-WhatIf` performs read-only discovery; no Docker build, registry login/push,
   app stop/start or configuration write is performed.
- By default each build gets a timestamp plus random-suffix release tag. Optional
   `-Image 'deployment-portal:release-20260917-1'` selects a new tag in the same
   repository. A tag found by preflight is refused. Use unique tags for concurrent
   releases; tag discovery is not a distributed publication lock. Deployment uses
   the Docker-reported digest verified against ACR, not a mutable tag lookup.
- `-PipConfigPath 'C:\approved\pip.ini'` passes an approved private-feed config
   as a BuildKit secret. The script does not display or store its contents.
- After a reviewed preview, unattended updates additionally require
   `-NonInteractive -Confirm:$false`. Routine updates do not require
   `-BackupConfirmed`; that switch is used only with `-MaintenanceReady`.
   Deployment approval is not proof that tests or security review were completed.
- Failed builds/pushes/digest verification do not change the app. After a stop or
   configuration failure the app may remain stopped; inspect the returned error
   and previous image before retrying. No automatic rollback or data reset occurs.
   `ImageUpdated` confirms configuration readback and start submission, not a
   successfully running image or employee sign-in. Verify those separately.

**Database migrations, including the WAL-era upgrade below:** stop the app and all
other database users, verify termination, and back up the complete stopped data
first. Then use `-DeployImage -MaintenanceReady -BackupConfirmed`. The script
checks the app's stopped state, but cannot certify all database users are gone;
it does not inspect the live database, take a backup, or detect migration needs.
Do not treat an ordinary image restart as the stopped-backup procedure.
Without `-BackupConfirmed`, interactive maintenance asks for backup acknowledgement;
unattended maintenance refuses to proceed. This requirement applies only when
`-MaintenanceReady` is selected, not to every later site update. Routine backup
and retention policies still apply independently of deployment prompts.

Edits to existing allowlisted site files are included in the image. When adding
new runtime files/assets, add their exact paths to both `.dockerignore` and
`SOURCE_FILES` in `tools/portal_source.py`, then review the exported image contents.
Do not replace these allowlists with a blanket directory copy: local databases,
credentials, internal catalogs and development artifacts must stay excluded.
Dependencies belong in `site/requirements.txt`. The source bundle embedded by the
image follows the same allowlist; catalog sync does not deploy these site changes.

### Upgrade from SQLite WAL

The portal now uses SQLite rollback journaling (`journal_mode=DELETE`) with
`synchronous=EXTRA`, which includes full synchronization and synchronizes the
directory after removing the rollback journal. Earlier images enabled WAL on
the App Service `/home` network share. [SQLite WAL requires shared memory and is
not supported across network filesystems](https://sqlite.org/wal.html).
App Service can briefly overlap retiring and replacement containers after
settings changes, including GitHub token storage; one configured instance is
not a guarantee that only one process is accessing the share during a restart.

This upgrade requires a maintenance window, not a rolling image replacement:

1. Stop the app and verify that all its application containers have terminated.
   Stop any other app, slot, job or database tool using the same database. Do not
   open a live WAL database from SCM, even read-only; WAL reads use a shared index.
2. Back up the complete stopped data directory and protected configuration to
   organization-controlled storage. Preserve the database and any `-wal`, `-shm`
   and `-journal` companions together, plus uploads and Terraform state. A WAL
   may contain committed changes absent from the main database. Do not delete,
   rename or truncate journal files as a repair. Test recovery from the backup
   on an isolated copy before proceeding.
3. Deploy the fixed image against the same database path and start one instance
   with one worker. SQLite performs the WAL-to-rollback conversion at startup,
   retaining readable committed WAL transactions. Startup fails rather than
   continuing if SQLite cannot acquire the conversion lock or accept rollback
   mode. Resolve remaining database users before retrying; do not reset data.
4. In the authenticated portal, verify the repository, processing settings,
   subscriptions, users and staged uploads. Restart once more and confirm that
   those values remain. Validate backup restoration on the actual hosting storage;
   rollback journaling still depends on correct filesystem locking and sync.
   Local tests cannot certify those properties for a particular network mount.
5. Do not roll back to a WAL-enabling image against a running database. Any rollback
   requires another stopped-app backup and a database-compatible recovery plan.

Conversion cannot reconstruct settings already lost before the upgrade. If the
source repository exists but Setup has no repository selection, use **Connect an
existing repository** and save the existing owner, name and publication branch.
Do not create the repository again or recopy its source. Verify the selection
after a page reload and restart. Previously staged submissions are not sent
automatically by reconnecting: retain them, download their bundles in Admin, and
have a maintainer import them through the normal review/merge process. Only then
sync the reviewed publication branch. Recovery does not auto-publish or deploy.

`GALLERY_ADMIN_OIDS` is an initial bootstrap eligibility list, not a live role
synchronization mechanism. Manage existing portal roles through authorized admin
controls. Retain an approved recovery administrator and host access procedure;
do not delete the database or disable authentication to recover access.

Before decommissioning, review active deployments and their cleanup obligations,
revoke integrations, remove only the intended role assignments/resources, and
apply company data retention/deletion policy. Stopping the portal alone does not
delete deployed workloads or stop their Azure charges.

### Reset application state

Run `tools/Reset-Portal.ps1` locally in PowerShell 7 with Azure CLI signed in to
the intended organization tenant. It is a deliberate reset, not a normal deployment
step. The operator needs App Service read/configuration/stop/start access and plan
read access. No Graph, GitHub, registry data-plane or resource-delete permission
is used. Do not run it to fix ordinary sign-in or sync errors.

The script keeps the current image and services. It stops the app, switches
`GALLERY_DB_PATH` and `GALLERY_TF_WORK` to a new random directory under
`/home/data/portal-reset/`, clears host-level GitHub/subscription fallback inputs,
and restarts after configuration readback. Users/roles, GitHub and subscription
Setup, catalog versions, staged uploads and history start empty in the new
directory. Entra registration, sign-in credentials, admin eligibility, managed
identity, Azure roles, networking, GitHub repositories/PRs and vault secrets are
not removed. Additional host authentication providers are also retained.

**Existing workloads are not deleted.** They continue incurring costs and the
fresh portal cannot automatically clean them up or show their history. Resolve
them first, or explicitly accept responsibility for manual cleanup. Quiesce jobs
and prevent other operators from deploying or changing settings during reset.
Do not use this on multiple apps sharing the same state. The script does not
query deployment records or certify that no workloads are active.

Preview (prompts for the hosting subscription, resource group and app):

```powershell
./tools/Reset-Portal.ps1 -WhatIf
```

After checking the target, backing up consistent data and protected app settings,
and reviewing outstanding workloads, apply:

```powershell
./tools/Reset-Portal.ps1 -AcknowledgeUnmanagedDeployments
```

Apply requires the exact app name and a high-impact confirmation. Parameters
`-SubscriptionId`, `-ResourceGroupName`, and `-AppName` can avoid discovery prompts.
Unattended use additionally requires `-NonInteractive -Confirm:$false`; the
workload acknowledgement is still mandatory. `-WhatIf` never stops the app or
changes settings. Only standard organization container and persistent data paths
are supported; unfamiliar paths and multi-instance hosting are rejected.

The returned non-secret plan records old and new paths. Save that plan in your
operations record. Old files remain in their original location: this is reversible
data detachment, **not secure erasure or a backup**, and does not revoke GitHub
tokens or delete Key Vault secrets. Apply retention/revocation policies separately.
On failure, inspect app state and its configured data paths before retrying. The
script does not automatically restart after a failed write/readback, or silently
roll back partially applied settings.

After success, verify container startup and sign in using an existing eligible
Entra administrator. Complete the fresh GitHub/subscription Setup yourself; the
script never claims the installation or bypasses authentication. To recover the
old installation, stop the app, restore **both** previous data paths and cleared
connection settings from the protected backup, verify them, then start. Do not
merge SQLite databases or resume overdue cleanup without reviewing the old
deployment records and current Azure resources.

## Troubleshooting

| Symptom | Checks and safe next steps |
| --- | --- |
| Docker engine unavailable | Start the Linux engine and build for `linux/amd64`; an installed Docker command alone is not a running engine. |
| TLS handshake failure downloading Python packages | Docker does not inherit host pip settings. Pass the approved configuration through the optional `pip_config` BuildKit secret and review corporate proxy/trust policy. Never disable certificate checks or select an unapproved mirror. |
| Registry image pull denied | Check the exact host identity and its registry data-plane role, registry permission mode, image name/digest, routing and DNS. For ACR, AcrPull is for RBAC-only mode; ABAC mode requires the applicable repository reader role. |
| `Container startup blocked` | Check HTTPS, persistent storage, attached identity/RBAC, enabled Entra provider, tenant-specific issuer, restricted audiences and GALLERY_ADMIN_OIDS. Network topology is not a startup requirement. Do not bypass authentication validation. |
| 401 at `/.auth/login/aad/callback` | Begin at the root URL, not the callback. Check the exact callback registration, tenant/client IDs, required ID-token issuance, credential expiry and Key Vault resolution, then review Easy Auth diagnostics and Entra sign-in logs. |
| Entra access/consent denied | Check enterprise-app user assignment, tenant membership, Conditional Access and company consent policy. Directory permissions, Azure roles and portal roles are separate. |
| Sign-in succeeds but Setup is forbidden | Check the user's object ID in this tenant and their persisted portal role. The first eligible configured user becomes initial admin; other employees and Azure Owners are not implicitly admins. |
| Organization-restricted site cannot be reached | The organization network owner must verify their chosen endpoints, DNS, routes and firewalls. The solution does not modify or bypass those restrictions. |
| Setup disappears after restart, or database is unwritable | Verify persistent `/home/data`, UID 10001 access and the expected database path. Stop and fix storage rather than create a second installation or change the process to root. |
| GitHub authorization or token storage fails | Check the organization OAuth client ID, device-flow enablement, organization policy/SSO, scopes, host write access and optional vault access. Reopen Setup after a settings-triggered restart; inspect partial repository creation before retrying. |
| Subscription lookup fails | Check Reader access for the host managed identity, not just the signed-in user; confirm the subscription is enabled and belongs to the configured tenant. |
| Negotiated pricing falls back to retail | Check supported billing agreement, API permissions and exact meter/currency/unit matching. Explicit fallback is expected when those conditions are not met. |
| Terraform is unavailable | The stock image does not include Terraform. Install and maintain an approved CLI in the organization image before enabling that executor; automatic download is disabled. |
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
organization-tenant deployment/cleanup test before enabling real workloads.