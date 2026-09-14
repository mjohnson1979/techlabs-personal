# Deployment Portal

Customer-operated Azure deployment platform. This branch contains the platform,
an empty catalog, and development tests/tools. Customers provide all workload IaC.

**Engineering preview; not approved for external publication or production use.**

## Setup overview

**Approved customer source -> private company repository -> customer container
registry -> private Azure App Service -> company Entra sign-in -> administrator
browser setup.**

The default setup uses a customer-owned Entra app registration configured before
the site opens. No publisher identity, setup code or source-code edits are needed.
The customer's platform team provisions Azure networking, hosting, identity and
permissions. The application does not grant itself permissions or create that
infrastructure. A secured Azure tenant does not make a web endpoint private.

This README covers the supported sign-in-first container path. The
[customer operations guide](customer/README.md) also covers optional identity
providers, credential storage and alternative installation modes. That guide
becomes the root README in the exported customer repository.

- [Prerequisites](#prerequisites)
- [1. Prepare the private host](#1-prepare-the-private-host)
- [2. Configure company Entra sign-in](#2-configure-company-entra-sign-in)
- [3. Set application configuration](#3-set-application-configuration)
- [4. Build and deploy the image](#4-build-and-deploy-the-image)
- [5. Complete browser setup](#5-complete-browser-setup)
- [6. Verify before enabling workloads](#6-verify-before-enabling-workloads)
- [Updates and operations](#updates-and-operations)
- [Troubleshooting](#troubleshooting)
- [Local preview and testing](#local-preview-and-testing)

## Prerequisites

| Area | Required before deployment |
| --- | --- |
| Source | Approved, history-free customer source in a company-controlled repository. Do not publish this development repository's history. |
| Workstation or build agent | Git, Docker with a running Linux engine and BuildKit, access to the Python base image and an approved Python package feed. Build for `linux/amd64`. |
| Azure environment | Azure public cloud, a customer-owned subscription and workforce tenant, and an App Service plan supporting Linux custom containers and private endpoints. Select the plan using your capacity and cost requirements. |
| Private access | Company network/VPN connectivity to the private endpoint, working private DNS, and a separate review of SCM/deployment endpoint access. |
| Registry | Customer-owned container registry; the build identity can push, and the App Service identity can pull the selected repository. |
| Entra | Permission to create/manage the customer app registration, configure its credentials and grant any consent required by company policy. |
| Azure administration | Permission to configure the host, network and registry, plus separately authorized role-assignment access for the managed identity. Azure Contributor alone cannot assign roles. |
| Portal administrator | The intended administrator's user object ID in the hosting tenant, not their email, the application ID or the managed identity's object ID. |
| Runtime and storage | Python 3.12 is included in the image. One worker and one App Service instance; persistent `/home/data` writable by container UID/GID `10001:10001`. |
| Optional integrations | Customer GitHub OAuth App with device flow enabled; existing Key Vault and permissions if selected; supported billing access for negotiated prices; customer-maintained Terraform CLI if Terraform execution is required. |

Entra registration ownership, Azure resource permissions and **portal roles are
different**. An employee's tenant membership, or their subscription Owner role,
does not automatically authorize portal setup. The application managed identity
performs Azure operations; the browser user's permissions are not delegated to it.

### Managed identity permissions

Enable the App Service's **system-assigned managed identity** and grant reviewed,
least-privilege roles. Role assignments are customer deployment work, not wizard actions.

| Purpose | Identity and scope |
| --- | --- |
| Startup discovery and subscription lookup | Host managed identity: Reader on the hosting subscription and on each deployment subscription to be added. All must belong to the same tenant. |
| Saving GitHub tokens or optional provider configuration | Host managed identity: Website Contributor on this App Service only, or a reviewed narrower equivalent. Startup itself only reads configuration. |
| Pulling an Azure Container Registry image | Host managed identity: `AcrPull` for RBAC-only registries, or `Container Registry Repository Reader` for ABAC-enabled registries, scoped/conditioned to the intended repository as appropriate. |
| Publishing the image | Build identity, separately from the host: approved push permissions, such as `AcrPush` or the corresponding ABAC repository writer role. |
| Deploying and cleaning up workloads | Host managed identity: workload-specific create/delete permissions in each selected subscription. Resource-group creation requires appropriate subscription-scope rights. Role-assigning workloads require separately reviewed, constrained role-assignment permissions. |
| Key Vault, if selected | Host identity: vault resource read plus secret Get, and Set only when the application will write credentials. Vault network access must work too. |

No Microsoft Graph application-creation permission is required for the default
sign-in-first flow. Additional provider-registration automation is optional and
has separate permissions documented in the customer guide.

## 1. Prepare the private host

1. Create or select a customer Linux App Service plan and container web app. Keep
	it unavailable to general users until configuration is complete; startup will
	remain blocked while required controls are missing.
2. Enable **HTTPS only**. Disable public network access and configure a private
	endpoint with DNS/routing from the company network or VPN. The container
	requires `publicNetworkAccess=Disabled`; a public endpoint with an IP allowlist
	is not equivalent for this implementation.
3. Configure private DNS resolution for the app and the SCM endpoint used by your
	deployment tooling. A private endpoint covers inbound access; private registry
	or Key Vault access may also need outbound VNet integration and DNS settings.
4. Enable the managed identity and grant the applicable roles above. Configure
	managed-identity image pulls; do not enable registry admin credentials merely
	to get past a permission error.
5. Enable persistent App Service storage. Ensure the mounted `/home/data` path is
	writable by UID 10001; the image's directory permissions do not necessarily
	determine the permissions of an external mount. Do not work around failures
	by running the application as root or broadly opening storage permissions.
6. Keep **one instance**. The image already starts one Gunicorn worker on port
	8000. Do not replace the image entrypoint or enable scale-out.

Allow the necessary outbound connections: Entra sign-in/discovery, Azure
management APIs, the registry and package feeds used by your build, GitHub when
enabled, retail/billing APIs for pricing, and Key Vault when selected. Company
proxy and Conditional Access policies still apply; never disable TLS validation.

## 2. Configure company Entra sign-in

Complete this in Azure/Entra before opening the application:

1. In the **tenant that owns the hosting subscription**, create or select a
	dedicated app registration with **Accounts in this organizational directory
	only** and platform **Web**. Alternatively, use **App Service > Authentication
	> Add identity provider > Microsoft > Create new app registration**.
2. Register the exact callback for the hostname employees will use:
	`https://<your-site>/.auth/login/aad/callback`. For an existing registration,
	enable **ID tokens (used for implicit and hybrid flows)** for Easy Auth's
	hybrid flow. Do not enable access-token implicit issuance just for this site.
3. Configure the Microsoft provider in **App Service > Authentication**, using
	the registration's client ID and tenant-specific issuer
	`https://login.microsoftonline.com/<tenant-id>/v2.0`. Do not use `common`,
	`organizations`, a publisher registration or the Azure CLI client ID.
4. Configure the provider credential in protected host settings, or use a Key
	Vault reference with the correct identity and network permissions. The portal
	can create the credential for a new registration. Record its expiry and
	rotation owner; never put it in source code, the image, chat or logs.
5. Require authentication for **all requests**, with no excluded paths. Select
	HTTP **302 redirect** to the Microsoft provider for unauthenticated browsers.
	Allow no other token audiences: the app's own client ID is implicitly accepted,
	and may also be listed explicitly. Keep HTTPS required in Easy Auth.
6. Apply your organization's user-assignment, consent and Conditional Access
	policies. Ensure the intended administrator is permitted to sign in. Restrict
	enterprise-application user assignment if only selected employees should have
	access; this does not replace the portal administrator setting below.

The container reads the provider through the Azure management API and verifies
its issuer, audience and hosting tenant before serving requests. It does not
create the registration, change authentication settings or read its secret at startup.

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

Use the approved customer repository, not an archive of the development Git
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
	select the customer registry, repository and release image. Prefer a digest
	reference where supported; otherwise use a non-reused release tag and record
	the resolved digest. Configure the host managed identity for image pulls.
3. Save and start/restart the app after the network, Entra and environment settings
	above are ready. Do not set a custom command that bypasses
	[the container entrypoint](site/server/container_start.py).
4. Check the deployment/build status and container startup logs. A pushed image
	or accepted deployment is not proof of successful startup. Allow for image
	pull/startup time; do not repeatedly redeploy solely because of an initial 5xx.

## 5. Complete browser setup

### First sign-in

From the company network/VPN, open the site's **root URL**, not the callback URL.
Easy Auth redirects to company Entra sign-in. Sign in as the designated portal
administrator. Setup opens automatically when there are no deployment
subscriptions, starting on **GitHub**. Reopen it later from the account menu.

Authentication shows the existing primary Entra connection. Adding another
Entra/OIDC provider is optional and is not part of the default onboarding.
Ordinary users can sign in if allowed by company policy, but cannot modify Setup.

### GitHub

1. If repository integration is needed, register a **customer-owned GitHub OAuth
	App**, enable device flow, and satisfy your organization's app-approval/SSO
	requirements. This is not a GitHub App installation or a publisher-owned app.
2. In **Setup > GitHub > GitHub OAuth application**, enter its public client ID
	and select **Save OAuth application**. No host-setting edit is needed.
3. Enable GitHub, choose the token storage destination, and select **Connect GitHub**.
	Complete authorization at `https://github.com/login/device` using the code
	displayed by the site. Review the `repo` and `read:org` scopes. Do not paste
	access tokens into the site; they are retained server-side in the selected store.
4. Select an accessible writable repository, or explicitly confirm creating a
	private repository and seeding the customer source. Existing repositories
	are not overwritten. Save the configuration and select **Sync catalog now**.

Host-setting changes during token storage can restart the app; reopen Setup to
resume. Disabling GitHub stops sync but does not revoke its token or delete the
last synchronized catalog. Revoke access in GitHub when it is no longer needed.
GitHub is optional; leave it disabled if not using this integration.

### Subscriptions and pricing

1. Grant the host managed identity Reader and the separately reviewed workload
	permissions on each intended deployment subscription. All subscriptions must
	be enabled and owned by the same Entra tenant as the host.
2. In **Setup > Subscriptions**, enter subscription IDs individually or import
	a UTF-8 CSV. `subscriptionId` is required; optional `pricingMode` is `retail`
	or `negotiated`. Do not enter display names, tenants or regions; Azure resolves them.
3. Review the resolved rows and select **Verify and save subscriptions**. Limits:
	200 subscriptions and 128 KiB per CSV. Failed lookups do not save partial lists.
4. Start with retail estimates unless your billing agreement and permissions
	support the negotiated-price API. Failed or ambiguous negotiated lookups
	explicitly fall back to retail; an estimate is not an invoice.

Adding a subscription does not grant permissions. Initial setup performs **no
quota checks**. The workload deployment view offers Compute quota checks later;
quota headroom does not guarantee physical capacity. Existing subscriptions
cannot currently be removed because tracked deployments may still require cleanup.

### Publish workloads

The initial catalog is intentionally empty. Publish reviewed customer-authored
runtime inputs through the configured repository and sync them. Bicep must be
compiled in the customer toolchain; the site does not compile it on sync. The
stock image has no Terraform CLI; install and maintain one in a reviewed customer
image before enabling Terraform execution. Uploaded workloads are executable code
with the host identity's permissions, not an untrusted-code sandbox.

See [repository input layout](customer/README.md#2-github) and
[workload publishing](customer/README.md#publish-your-own-workloads) for formats and limits.

## 6. Verify before enabling workloads

- [ ] The root URL resolves and loads from the intended company network/VPN; public access is denied.
- [ ] Unauthenticated users are redirected to the configured Entra provider; wrong-tenant users are rejected.
- [ ] The designated administrator reaches Setup without a code; an ordinary tenant member cannot read or change administrator setup.
- [ ] GitHub authorization and catalog sync succeed if enabled; secrets are absent from responses and logs.
- [ ] Subscription lookup returns Azure names and the correct tenant; deployment targets outside the allowlist are refused.
- [ ] A reviewed disposable workload can deploy and be deleted/cleaned up with the managed identity's actual permissions.
- [ ] An app restart retains users, setup, catalog versions and deployment records. Test backups and restoration before production use.
- [ ] Costs, log retention, credential rotation and operational ownership have been approved.

Local tests do not establish live Entra, private networking or workload deployment
readiness. Perform these checks in a customer-controlled non-production environment first.

## Updates and operations

Build and scan a new image, retain the previous digest, back up persistent data
and protected configuration, and deploy through your customer change process.
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

## Troubleshooting

| Symptom | Check and safe next action |
| --- | --- |
| Docker cannot connect to its engine | Start Docker Desktop's Linux engine and confirm the build platform is `linux/amd64`. |
| TLS failure downloading Python packages | Use the approved pip configuration through `--secret id=pip_config,...`. Inspect corporate proxy/trust policy; never disable certificate verification. |
| Registry image pull denied | Check the exact host identity, pull role and registry permission mode, image digest/tag, outbound routing and DNS. Azure resource Reader is not registry data-plane pull access. |
| `Container startup blocked` | Check private access disabled, HTTPS, Easy Auth enabled with authentication required, persistent storage, managed identity/RBAC, tenant-specific provider and `GALLERY_ADMIN_OIDS`. Startup deliberately withholds sensitive configuration values. |
| 401 at `/.auth/login/aad/callback` | Start from the root URL. Check the exact registered callback, tenant/client IDs, ID-token issuance, credential expiry/reference resolution, Easy Auth diagnostics and Entra sign-in logs. A callback opened directly cannot establish a session. |
| Sign-in works but Setup returns 403 | Check the user's object ID in this tenant and the stored portal role. Tenant membership or Azure Owner does not confer portal admin rights. |
| App inaccessible on VPN | Verify private endpoint approval, private DNS including SCM where needed, routes and firewalls. Do not enable public access as a workaround. |
| Restart loses setup or database is unwritable | Verify persistent `/home/data` and UID 10001 permissions; stop and fix storage rather than create another installation. |
| GitHub authorization or save fails | Check customer OAuth client ID, enabled device flow, organization policy/SSO, requested scopes and host/Key Vault write permissions. Reopen Setup after a host restart. |
| Subscription lookup fails | Check the host managed identity's Reader access, subscription state and same-tenant ownership. Browser-user access is not sufficient. |
| Negotiated pricing falls back to retail | Check supported billing agreement, billing permissions and exact meter/currency/unit matching; fallback is explicit expected behavior. |
| Local preview rejects saves with 405 | Expected: it is read-only and uses a synthetic identity. Use the private Azure host for real sign-in and cloud operations. |

Do not paste credentials, tokens, auth cookies or unredacted configuration/log
dumps into tickets. Share only the relevant status, timestamp and sanitized error.

## Local preview and testing

To inspect the UI without Azure, install Python 3.12 and run:

```powershell
python tools/preview_customer.py
```

Open `http://127.0.0.1:8766/`. The preview is loopback-only, read-only, and uses
synthetic users/data; it cannot test actual Entra sign-in or deploy resources.
The production image entrypoint intentionally refuses standalone local startup
without its required App Service environment. Do not disable production checks
or expose a preview server to make a local demo look like a hosted installation.

## Reference and license

- [Detailed customer setup and operations](customer/README.md)
- [App Service Entra configuration](https://learn.microsoft.com/azure/app-service/configure-authentication-provider-aad)
- [Custom container configuration](https://learn.microsoft.com/azure/app-service/configure-custom-container)
- [App Service private endpoints](https://learn.microsoft.com/azure/app-service/overview-private-endpoint)
- [Registry managed-identity authentication](https://learn.microsoft.com/azure/container-registry/container-registry-authentication-managed-identity)
- [Key Vault references](https://learn.microsoft.com/azure/app-service/app-service-key-vault-references)
- [MIT License](LICENSE) and [security reporting](SECURITY.md)

The MIT License includes its AS IS warranty disclaimer and limitation of
liability. Retain its copyright and permission notices. No support SLA, trademark
rights or endorsement are granted; dependencies retain their own licenses.
Customer operation does not imply that no personal data is processed. Complete
legal, privacy, security and export reviews before external distribution or use.

## Local development

Use Python 3.12 and the repository virtual environment. Runtime dependencies are
declared only in [site/requirements.txt](site/requirements.txt).

```powershell
python -m pytest site/server/tests
python tools/build_customer_distribution.py --output runs/customer-review.zip
```

The builder refuses to overwrite an archive. Extract the generated archive into
a new directory and run `python tools/preview_customer.py` from that directory
for a read-only browser preview. Actual hosting requires the customer-owned
authentication and managed identity configuration documented in the setup guide.

See [contributing](CONTRIBUTING.md) and the [release review checklist](customer/RELEASE-CHECKLIST.md).

## Distribution boundary

Bundled workloads, shared infrastructure modules, internal snapshots, old branded
assets and internal deployment workflows have been removed from this branch.
Tests, editor instructions, release notes and build tooling are development-only;
the export builder includes only the customer allowlist and empty data snapshots.
Local environments, credentials, databases, logs and preview artifacts are not
release inputs. No automatic workflow deploys this branch to an internal service.

**Deletion is not history removal.** The existing Git history still contains the
original content. Publish only an approved, history-free export into a new
repository; never publish a fork or mirror of this repository. The required
Microsoft copyright notice and MIT disclaimer remain intact.