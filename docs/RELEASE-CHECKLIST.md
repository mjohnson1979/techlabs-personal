# Organization distribution release gate

Internal working document. Not included in the organization export.

Branch: `customer/self-hosted-platform`, based on `dev` at `7e483880`.
At branch creation `dev`, `origin/dev`, and `origin/main` were equal; local `main`
was older and was not changed. No source history is authorized for publication.

## Build and verify

From the repository root, in the existing virtual environment:

```powershell
python -m pytest site/server/tests
python tools/build_distribution.py --output runs/organization-platform.zip
```

The builder refuses to overwrite an existing archive. Use a new filename for a
new candidate. Review the archive and `MANIFEST.json`, not just the branch diff.
Do not release the originating repository, a GitHub branch archive, or a fork.
Bundled internal IaC, catalog content and internal deployment automation have been
removed from this branch's working tree. They remain in Git history. Development
tests, editor instructions and local build output are still excluded by the
organization export allowlist; a cleaned working tree is not a history-free release.

## Required decisions before external publication

- [ ] CELA and OSPO: authorization to release the platform source under the
  existing Microsoft MIT copyright notice; provenance and rights for all source.
- [ ] Third-party licenses, notices, dependency inventory/SBOM and vulnerability
  scan, including server runtime, JavaScript, Terraform CLI and providers if used.
- [ ] Trademark review: neutral product name, no Microsoft logos/service icons;
  technical service names and required copyright/license notices retained.
- [ ] Export/trade review: no conclusion of exemption solely from removing IaC.
- [ ] Confirm applicable privacy process. The supplied TrIP response says no
  specific TrIP review requirement was identified for the described model; it
  does not approve changed data flows or establish that cloud services process
  no personal data. Reassess if the operator, telemetry, support or hosting changes.
- [ ] Security review and threat model: Easy Auth ingress cannot be bypassed,
  tenant/audience checks, CSRF and rate limiting, file-upload abuse, executable
  infrastructure supply chain, admin controls, and least-privilege deployment MI.
- [ ] Identify the release owner, vulnerability-reporting channel, maintenance
  policy and approved support/disclaimer language. Do not imply a product SLA.
- [ ] Manual confidentiality review of every allowlisted file and dependency.
  Regex checks detect known strings, not all possible confidential information.
- [ ] Live test in an isolated organization-owned tenant/subscription: correct and
  incorrect tenant sign-in, explicit admin bootstrap, own IaC publishing,
  deployment, cost view, lease cleanup, failed cleanup, and recovery after restart.
- [ ] Organization retention/deletion, backup/restore and incident runbooks for
  identity claims, audit records, uploaded code, logs and Terraform state.

## Engineering scope

The initial package has an empty catalog and blank organization IDs, enforced
single-Azure-tenant/multiple-subscription configuration, explicit admin bootstrap,
managed-identity-only deployment, and no publisher telemetry integration.
Legacy OAuth/external-subscription flows are blocked in the package. It requires
organization-controlled App Service Authentication and one worker/one app instance.

The export is an engineering preview, not evidence of release approval or a
production security certification. Publishing, live deployment and cloud changes
are separate actions and are not performed by the builder.

## Hosted setup verification

- [ ] Build the image from a fresh organization export/private clone, scan the final
  image and dependency licenses, and pin the deployed image/base digests.
- [ ] Run as UID 10001 on authenticated App Service with persistent `/home/data`, system
  identity, preconfigured Entra and explicit GALLERY_ADMIN_OIDS, without a setup
  secret or manually duplicated tenant/app/host IDs. Confirm startup refuses
  public ingress, disabled Easy Auth, extra audiences, wrong tenant, absent admins,
  missing storage or wrong host identity; startup must not write Azure settings.
- [ ] Verify an ordinary tenant member visiting first remains a guest and cannot
  access Setup. The designated administrator signs in and starts with GitHub;
  missing auth never falls back to anonymous or code installation.
- [ ] Confirm the single setup secret's persisted expiry does not renew on restart
  and previously used secrets cannot reactivate installation after rotation.
- [ ] Save the company GitHub OAuth client ID in browser Setup and verify device
  authorization without preconfiguring GALLERY_GITHUB_OAUTH_CLIENT_ID.
- [ ] Exercise first-run browser installation with no Entra provider configured:
  one-time code, expiry, lost-session rotation, primary connection, same-browser
  sign-in verification and automatic lockdown. Reject wrong tenant/audience,
  forged identity without setup session, replay and concurrent setup attempts.
- [ ] Verify Easy Auth remains enabled during installation and strips supplied
  identity headers. Confirm normal APIs/jobs remain blocked, setup sessions survive
  restart and secrets never appear in validation errors, logs or database records.
- [ ] Test App Service and Key Vault writes interrupted by host restart, failed
  activation recovery and permanently disabled installer after completion.
- [ ] Verify additive Entra/OIDC configuration on a disposable organization App Service;
  retain working bootstrap auth and confirm issuer/audience/subject claims.
- [ ] Confirm Entra credential storage, expiry/rotation and recovery after app restart.
  Verify generated secrets never appear in API responses, DB records or logs.
- [ ] Confirm GitHub repository creation permissions, empty-repo seeding and recovery
  from partial writes. Protect the catalog branch before executing imported code.
- [ ] Verify no active workflows are exported/seeded by default. Validate opt-in
  generated YAML with actionlint, SHA-pinned actions, reviewed-branch inputs, separate
  publication permissions, source-commit guards and protected-branch failures.
- [ ] Test Create workflow PR on an existing repository: admin/origin/confirmation
  checks, workflow scope, idempotent review links, no automatic merge or setup reset.
- [ ] Validate Bicep wrapper compilation, metadata-bound public retail refresh and
  atomic snapshot sync. Confirm invalid feeds retain the last good catalog.
- [ ] Verify container validation, pricing and quota refresh with runners unavailable
  and runner probes denied. Test restart recovery, admin/origin checks, rate limits,
  and immutable deployment artifacts. Verify unavailable choices are disabled.
- [ ] Exercise real compilers inside the target host's Landlock/seccomp sandbox:
  local modules/parameters succeed; database/process-environment reads, network
  access, long-running work and unsupported kernels fail closed. No compiler
  output or environment may expose credentials. Review compiler/license updates.
- [ ] Enable capacity only after organization OIDC/Reader approval; verify the selected
  tenant/subscriptions/regions, GET-only collection, no negotiated rates in Git,
  snapshot age and configured-subscription filtering in the portal.
- [ ] Verify CSV atomic failure and allowlist enforcement across multiple subscriptions,
  including lease cleanup and organization Terraform state.
- [ ] Validate supported negotiated price sheets against invoices and verify the UI's
  retail fallback. Compute quota coverage is not all-service or physical capacity.
- [ ] Review the additional setup identity privileges (Graph app creation and host
  configuration) and remove them after onboarding if no longer needed.