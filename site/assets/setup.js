(function () {
  "use strict";
  const el = window.TechlabsDom.el;
  const panel = window.TechlabsPanel;
  const trigger = document.getElementById("nav-setup");

  async function request(path, method = "GET", data, signal) {
    const response = await fetch("/api/admin/setup" + path, {
      method, credentials: "same-origin",
      signal,
      headers: { "Content-Type": "application/json" },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    const uncertainWrite = method === "GET" ? "" : " The operation may have completed. Reload Setup and inspect the target before retrying." +
      (path === "/github" && data?.create ? " Do not create another repository to recover." : "");
    if (response.redirected || response.status === 401) {
      throw new Error("Your portal session needs sign-in. Open the portal root, sign in, then reopen Setup." + uncertainWrite);
    }
    const content = await response.text();
    let result;
    try {
      result = JSON.parse(content);
    } catch {
      throw new Error(`Setup received an empty or non-JSON response (HTTP ${response.status}). The app or gateway may have restarted or timed out.` + uncertainWrite);
    }
    if (!result || typeof result !== "object" || Array.isArray(result)) {
      throw new Error(`Setup received an unexpected response (HTTP ${response.status}). Reload Setup to check the current state.` + uncertainWrite);
    }
    if (!response.ok) {
      if (typeof result.detail === "string") throw new Error(result.detail);
      if (Array.isArray(result.detail) && path.startsWith("/subscriptions")) {
        const guidance = {
          subscriptionId: "Subscription ID must be a nonzero GUID, not a name or Azure resource path.",
          pricingMode: "Pricing preference must be Public retail or Negotiated.",
          subscriptions: "Add at least one subscription before saving (maximum 200).",
          csv: "Choose a CSV with a subscriptionId column and optional pricingMode column (maximum 128 KiB).",
        };
        const messages = result.detail.slice(0, 10).map(issue => {
          const location = Array.isArray(issue?.loc) ? issue.loc : [];
          const fieldName = location[location.length - 1];
          const rowIndex = location.find(part => Number.isInteger(part) && part >= 0 && part < 200);
          const message = Object.hasOwn(guidance, fieldName) ? guidance[fieldName] : "Subscription request validation failed. Reload the portal and retry; if it persists, check the deployed API version.";
          return rowIndex === undefined ? message : `Row ${rowIndex + 1}: ${message}`;
        });
        if (messages.length) throw new Error([...new Set(messages)].join(" "));
      }
      throw new Error(`Setup request failed (HTTP ${response.status}). Check the setup fields and current connection.` + uncertainWrite);
    }
    return result;
  }

  function field(label, value = "", type = "text") {
    const control = el("input", { type, class: "deploy-input", value, "aria-label": label });
    return { control, node: el("label", { class: "setup-field" }, [el("span", { text: label }), control]) };
  }

  function choice(label, values, selected) {
    const control = el("select", { class: "deploy-input", "aria-label": label }, values.map(([value, text]) => el("option", { value, text })));
    control.value = selected;
    return { control, node: el("label", { class: "setup-field" }, [el("span", { text: label }), control]) };
  }

  function button(text, action, status) {
    const control = el("button", { type: "button", class: "deploy-btn-secondary", text });
    control.addEventListener("click", async () => {
      control.disabled = true;
      status.textContent = "Working...";
      try { await action(); } catch (error) { status.textContent = error.message; }
      finally { control.disabled = false; }
    });
    return control;
  }

  async function open() {
    panel.open((body) => {
      body.appendChild(el("h2", { text: "Initial setup" }));
      const status = el("p", { class: "setup-status", role: "status", text: "Loading configuration..." });
      const tabs = el("div", { class: "tabs setup-tabs", role: "tablist", "aria-label": "Setup steps" });
      const content = el("section", { class: "setup-content", role: "tabpanel" });
      body.append(tabs, status, content);
      request("").then((settings) => {
        status.textContent = "Authenticated administrator setup";
        const builders = [
          ["1. Authentication", () => authentication(content, settings, status)],
          ["2. GitHub", () => github(content, settings, status)],
          ["3. Subscriptions", () => subscriptions(content, settings, status)],
          ["4. Branding", () => branding(content, settings, status)],
        ];
        const initialTab = settings.primaryAuthentication?.clientId ? 1 : 0;
        builders.forEach(([title, build], index) => {
          const tab = el("button", { type: "button", role: "tab", class: "tab", text: title, "aria-selected": index === initialTab ? "true" : "false" });
          tab.addEventListener("click", () => {
            tabs.querySelectorAll("button").forEach(node => node.setAttribute("aria-selected", "false"));
            tab.setAttribute("aria-selected", "true");
            content.replaceChildren();
            status.textContent = "";
            build();
          });
          tabs.appendChild(tab);
        });
        builders[initialTab][1]();
      }).catch(error => { status.textContent = error.message; });
    });
  }

  function branding(body, settings, status) {
    const saved = settings.branding || {};
    const name = field("Site name", saved.brandName || "Deployment Portal");
    name.control.maxLength = 80;
    name.control.required = true;
    const footer = field("Footer disclaimer", saved.footerText ?? "Cost estimates are indicative; verify against your cloud agreement.");
    footer.control.maxLength = 500;
    const save = button("Save branding", async () => {
      if (!name.control.value.trim()) { name.control.reportValidity(); throw new Error("Enter a site name."); }
      const result = await request("/branding", "PUT", { brandName: name.control.value.trim(), footerText: footer.control.value.trim() });
      settings.branding = result;
      document.dispatchEvent(new CustomEvent("techlabs:branding-saved", { detail: result }));
      status.textContent = "Branding saved.";
    }, status);
    body.append(el("div", { class: "setup-branding" }, [el("h3", { text: "Site branding" }), name.node, footer.node, el("div", { class: "setup-actions" }, [save])]));
  }

  function authentication(body, settings, status) {
    const primary = settings.primaryAuthentication || {};
    body.appendChild(el("h3", { text: "Primary Entra connection" }));
    body.appendChild(el("p", { class: "setup-status", text: primary.verified ? "Sign-in verified" : "Configured in App Service" }));
    body.appendChild(el("p", { class: "setup-status", text: `Tenant: ${settings.azureTenantId || ""}` }));
    if (primary.clientId) body.appendChild(el("p", { class: "setup-status", text: `Application: ${primary.clientId}` }));
    const additional = el("details", {}, [el("summary", { text: "Additional identity provider (optional)" })]);
    body.appendChild(additional);
    body = additional;
    const saved = Object.keys(settings.authentication || {}).length ? settings.authentication : (settings.createdRegistration || {});
    const kind = choice("Identity provider", [["entra", "Microsoft Entra ID"], ["oidc", "Other OpenID Connect provider"]], saved.kind || "entra");
    const name = field("Provider name", saved.providerName || "organization");
    const title = field("Registration display name", "Deployment Portal");
    const origin = field("Public application URL", saved.publicUrl || (location.protocol === "https:" ? location.origin : ""), "url");
    const client = field("Client ID", saved.clientId || "");
    const issuer = field("Issuer URL", saved.issuer || `https://login.microsoftonline.com/${settings.azureTenantId}/v2.0`, "url");
    const discovery = field("Discovery document URL", saved.discoveryUrl || `https://login.microsoftonline.com/${settings.azureTenantId}/v2.0/.well-known/openid-configuration`, "url");
    const initialStorage = saved.secretStorage || (saved.kind === "oidc" && !saved.secretSettingName ? "none" : "app_service");
    const storage = choice("Client secret storage", [["app_service", "App Service settings"], ["key_vault", "Azure Key Vault"], ["none", "No client secret (OIDC only)"]], initialStorage);
    const secret = field("Secret app-setting name", saved.secretSettingName || `PORTAL_${name.control.value.toUpperCase()}_AUTH_SECRET`);
    const vault = field("Key Vault resource ID", saved.keyVaultResourceId || "");
    const vaultSecret = field("Key Vault secret name", saved.keyVaultSecretName || `portal-${name.control.value.replace(/_/g, "-")}-auth-secret`);
    const storageStatus = el("p", { class: "muted", role: "status" });
    body.appendChild(el("h3", { text: "Add an identity provider" }));
    body.appendChild(el("p", { class: "muted", text: "The existing sign-in provider stays enabled." }));
    body.appendChild(el("div", { class: "setup-grid" }, [kind.node, name.node, title.node, origin.node, client.node, issuer.node, discovery.node, storage.node, secret.node, vault.node, vaultSecret.node]));
    body.appendChild(storageStatus);
    function storageFields() {
      const mode = storage.control.value;
      return {
        secretStorage: mode,
        secretSettingName: mode === "none" ? "" : secret.control.value,
        keyVaultResourceId: mode === "key_vault" ? vault.control.value : "",
        keyVaultSecretName: mode === "key_vault" ? vaultSecret.control.value : "",
      };
    }
    function updateStorage() {
      const none = storage.control.querySelector('option[value="none"]');
      none.disabled = kind.control.value !== "oidc";
      if (none.disabled && storage.control.value === "none") storage.control.value = "app_service";
      const mode = storage.control.value;
      secret.node.hidden = mode === "none";
      vault.node.hidden = vaultSecret.node.hidden = mode !== "key_vault";
      storageStatus.textContent = mode === "none"
        ? "No client credential will be configured. The OIDC provider must permit authentication without a client secret."
        : mode === "key_vault"
          ? "Existing organization vault required. The host's Key Vault reference identity must be the setup managed identity with secret access. App Service stores a reference only."
          : "The client secret is stored in an encrypted App Service setting. Existing registrations require that setting to be configured first.";
    }
    storage.control.addEventListener("change", updateStorage);
    kind.control.addEventListener("change", updateStorage);
    updateStorage();
    const callback = el("p", { class: "setup-status" });
    const updateCallback = () => { callback.textContent = `Callback: ${origin.control.value.replace(/\/$/, "")}/.auth/login/${name.control.value}/callback`; };
    origin.control.addEventListener("input", updateCallback);
    name.control.addEventListener("input", updateCallback);
    updateCallback();
    body.appendChild(callback);
    const register = button("Create Entra registration", async () => {
      const target = storage.control.value === "key_vault" ? "the selected organization Key Vault, with only a reference in App Service" : "an encrypted App Service setting";
      if (!confirm(`Create an Entra application and service principal, generate a 180-day client credential and store it in ${target}? App settings may restart the host.`)) { status.textContent = "Cancelled."; return; }
      const result = await request("/authentication/register", "POST", { displayName: title.control.value, publicUrl: origin.control.value, providerName: name.control.value, ...storageFields(), confirm: true });
      client.control.value = result.clientId;
      issuer.control.value = result.issuer;
      discovery.control.value = result.discoveryUrl;
      secret.control.value = result.secretSettingName || "";
      storage.control.value = result.secretStorage || "app_service";
      updateStorage();
      status.textContent = `Registration created. Credential stored in ${result.secretStorage === "key_vault" ? "Key Vault" : "App Service"}, expires ${result.credentialExpiresAt || "as configured"}. Add the provider after the host restart.`;
    }, status);
    const apply = button("Add provider to App Service", async () => {
      if (!confirm("Add this provider to App Service Authentication while retaining the current sign-in provider?")) { status.textContent = "Cancelled."; return; }
      const result = await request("/authentication", "PUT", {
        kind: kind.control.value, providerName: name.control.value, clientId: client.control.value,
        issuer: issuer.control.value, discoveryUrl: discovery.control.value, publicUrl: origin.control.value,
        ...storageFields(), confirm: true,
      });
      settings.authentication = result;
      status.replaceChildren(document.createTextNode("Provider added. "), el("a", { href: result.loginUrl, target: "_blank", rel: "noopener noreferrer", text: "Test sign-in" }));
    }, status);
    kind.control.addEventListener("change", () => { register.hidden = kind.control.value !== "entra"; });
    register.hidden = kind.control.value !== "entra";
    body.appendChild(el("div", { class: "setup-actions" }, [register, apply]));
  }

  function github(body, settings, status) {
    let saved = settings.github || {};
    let connected = !!settings.githubConnected;
    let discovered = { owners: [], repositories: [] };
    let flowId = "";
    let timer = null;
    let polling = false;
    let processing = settings.processing || { mode: "container", runnerLabels: [], capacityRegions: [] };
    let runnerProbe = settings.runnerProbe || {};
    let jobTimer = null;
    const enabled = field("Enable GitHub", "", "checkbox");
    enabled.control.checked = !!saved.enabled;
    const mode = choice("Repository action", [["connect", "Connect an existing repository"], ["create", "Create a private repository and copy source"], ["copy", "Copy portal source to an empty or LICENSE-only repository"]], "connect");
    const owner = choice("GitHub account", [], "");
    const repositories = choice("Repository", [], "");
    const repo = field("New repository name", "deployment-portal");
    const account = el("p", { class: "muted", text: connected ? "GitHub connection available." : "Not connected to GitHub." });
    const device = el("div", { class: "setup-status", hidden: true });
    const oauthClient = field("GitHub OAuth App client ID", settings.githubOAuthClientId || "");
    const oauthSave = button("Save OAuth application", async () => {
      if (!confirm("Use this organization-owned GitHub OAuth App for device sign-in?")) { status.textContent = "Cancelled."; return; }
      const result = await request("/github/oauth-client", "PUT", { clientId: oauthClient.control.value.trim(), confirm: true });
      settings.githubOAuthClientId = result.clientId;
      settings.githubDeviceConfigured = result.configured;
      toggle();
      status.textContent = "GitHub OAuth application saved.";
    }, status);
    const oauthSettings = el("details", { open: !settings.githubDeviceConfigured }, [
      el("summary", { text: "GitHub OAuth application" }), oauthClient.node,
      el("div", { class: "setup-actions" }, [oauthSave, el("a", { href: "https://github.com/settings/developers", target: "_blank", rel: "noopener noreferrer", text: "GitHub developer settings" })]),
    ]);
    const credential = settings.githubCredentialStorage || {};
    const storage = choice("GitHub token storage", [["app_service", "App Service settings"], ["key_vault", "Azure Key Vault"]], credential.secretStorage || "app_service");
    const vault = field("GitHub Key Vault resource ID", credential.keyVaultResourceId || "");
    const vaultSecret = field("GitHub Key Vault secret name", credential.keyVaultSecretName || "github-token");
    const advanced = el("details", {}, [el("summary", { text: "Token storage (advanced)" }), el("div", { class: "setup-grid" }, [storage.node, vault.node, vaultSecret.node])]);
    const selection = el("div", { class: "setup-grid" }, [mode.node, repositories.node, owner.node, repo.node]);
    const processingMode = choice("Catalog processing", [["container", "Portal container"], ["github-hosted", "GitHub-hosted runners"], ["self-hosted", "Self-hosted runners"]], processing.mode);
    const runnerLabels = choice("Verified Linux runner", [], "");
    const capacityRegions = field("Capacity regions (comma-separated)", (processing.capacityRegions || []).join(", "));
    const runnerStatus = el("p", { class: "setup-status" });
    const jobStatus = el("p", { class: "setup-status", text: settings.processingJob?.detail || "No processing run requested." });

    function showRunnerProbe() {
      const matches = runnerProbe.repository === `${saved.owner}/${saved.repository}`;
      for (const option of processingMode.control.options) {
        if (option.value !== "container") option.disabled = !matches || !runnerProbe[option.value]?.available;
      }
      const candidates = matches ? runnerProbe["self-hosted"]?.candidates || [] : [];
      runnerLabels.control.replaceChildren(...candidates.map(item => el("option", { value: JSON.stringify(item.labels), text: item.labels.join(", ") + (item.busy ? " (busy)" : "") })));
      if (processing.runnerLabels?.length) runnerLabels.control.value = JSON.stringify(processing.runnerLabels);
      runnerLabels.node.hidden = processingMode.control.value !== "self-hosted";
      runnerStatus.textContent = !matches ? "Runner access has not been checked for this repository. Portal container is available."
        : `GitHub-hosted: ${runnerProbe["github-hosted"]?.detail || "Unverified"} Self-hosted: ${runnerProbe["self-hosted"]?.detail || "Unverified"}`;
      if (processingMode.control.selectedOptions[0]?.disabled) processingMode.control.value = "container";
    }

    async function refreshJob() {
      clearTimeout(jobTimer);
      if (!body.isConnected) return;
      try {
        const result = await request("/github/processing");
        jobStatus.textContent = `${result.job.status}: ${result.job.detail || ""} ${result.job.capacityDetail || ""} ${result.effective.detail || ""}`;
        if (result.job.status === "queued" || result.job.status === "running") jobTimer = setTimeout(refreshJob, 3000);
        else if (result.job.status === "succeeded") document.dispatchEvent(new CustomEvent("techlabs:setup-saved"));
      } catch (error) { jobStatus.textContent = error.message; }
    }

    const checkRunners = button("Check runner access", async () => {
      runnerProbe = await request("/github/runners/probe", "POST", {});
      showRunnerProbe();
      status.textContent = "Runner check complete. Unavailable or unverified runner choices are disabled.";
    }, status);
    const saveProcessing = button("Save processing settings", async () => {
      if (!confirm("Save the processing location and quota regions? Unavailable runners fall back to the portal container. Existing repository workflows are not deleted or disabled.")) return;
      processing = await request("/github/processing", "PUT", {
        mode: processingMode.control.value,
        runnerLabels: processingMode.control.value === "self-hosted" ? JSON.parse(runnerLabels.control.value || "[]") : [],
        capacityRegions: capacityRegions.control.value.split(/[\s,]+/).filter(Boolean), confirm: true,
      });
      settings.processing = processing;
      status.textContent = "Processing settings saved.";
      toggle();
      await refreshJob();
    }, status);
    processingMode.control.addEventListener("change", () => { runnerLabels.node.hidden = processingMode.control.value !== "self-hosted"; });
    const processingSection = el("div", {}, [el("h3", { text: "Catalog processing" }), el("div", { class: "setup-grid" }, [processingMode.node, runnerLabels.node, capacityRegions.node]), runnerStatus,
      el("div", { class: "setup-actions" }, [checkRunners, saveProcessing]), jobStatus]);

    async function discover() {
      discovered = await request("/github/discover");
      connected = true;
      account.textContent = `Connected as ${discovered.account.login}.`;
      owner.control.replaceChildren(...discovered.owners.map(item => el("option", { value: item.login, text: item.login })));
      repositories.control.replaceChildren(el("option", { value: "", text: "Select repository" }), ...discovered.repositories.map(item => el("option", { value: item.fullName, text: item.fullName })));
      if (saved.owner) owner.control.value = saved.owner;
      if (saved.repository) repositories.control.value = `${saved.owner}/${saved.repository}`;
      if (saved.enabled && (runnerProbe.repository !== `${saved.owner}/${saved.repository}` || Date.now() / 1000 - (runnerProbe.checkedAt || 0) > 600)) {
        request("/github/runners/probe", "POST", {}).then(result => { runnerProbe = result; showRunnerProbe(); })
          .catch(error => { runnerStatus.textContent = `${error.message} Portal container remains available.`; });
      }
      status.textContent = discovered.truncated ? "Showing the first 500 accessible repositories/organizations; narrow access in GitHub if the target is missing." : "Account and repositories loaded from GitHub.";
      toggle();
    }

    async function poll() {
      if (!body.isConnected || !enabled.control.checked || !flowId || polling) return;
      clearTimeout(timer);
      polling = true;
      status.textContent = "Checking GitHub sign-in and verifying the account...";
      toggle();
      try {
        const result = await request("/github/device/poll", "POST", { flowId }, AbortSignal.timeout(45000));
        if (result.status === "pending") {
          status.textContent = "Waiting for GitHub authorization. The next check runs automatically.";
          timer = setTimeout(poll, Math.max(1, result.interval || 5) * 1000);
        } else if (result.status === "connected") {
          flowId = ""; device.hidden = true;
          await discover();
        } else {
          flowId = "";
          status.textContent = result.message || "GitHub sign-in failed. Connect again.";
          toggle();
        }
      } catch (error) {
        const detail = error.name === "TimeoutError" ? "The sign-in check timed out." : error.message;
        status.textContent = flowId
          ? `${detail} Check sign-in retries verification. If the host restarted or the code expired, cancel sign-in and connect again.`
          : `${detail} Reopen Setup to reload the saved GitHub connection.`;
      } finally { polling = false; toggle(); }
    }

    const connect = button("Connect GitHub", async () => {
      if (flowId) { await poll(); return; }
      const destination = storage.control.value === "key_vault" ? "the selected Key Vault" : "encrypted App Service settings";
      if (!confirm(`Authorize GitHub repository, organization and workflow-file access? The token will be stored server-side in ${destination} for background sync and reviewed workflow installation. Host settings may restart the app.`)) { status.textContent = "Cancelled."; return; }
      const result = await request("/github/device/start", "POST", {
        secretStorage: storage.control.value, secretSettingName: "GALLERY_GITHUB_TOKEN",
        keyVaultResourceId: storage.control.value === "key_vault" ? vault.control.value : "",
        keyVaultSecretName: storage.control.value === "key_vault" ? vaultSecret.control.value : "", confirm: true,
      });
      flowId = result.flowId;
      device.replaceChildren(el("p", { text: "GitHub verification code" }), el("strong", { class: "setup-device-code", text: result.userCode }), el("p", {}, el("a", { href: "https://github.com/login/device", target: "_blank", rel: "noopener noreferrer", text: "Authorize on GitHub" })));
      device.hidden = false;
      status.textContent = "Waiting for GitHub authorization...";
      toggle();
      timer = setTimeout(poll, Math.max(1, result.interval) * 1000);
    }, status);
    const cancel = button("Cancel sign-in", async () => {
      clearTimeout(timer);
      if (flowId) await request("/github/device/cancel", "POST", { flowId });
      flowId = ""; device.hidden = true; toggle(); status.textContent = "Sign-in cancelled.";
    }, status);
    const save = button("Save GitHub configuration", async () => {
      const create = mode.control.value === "create" && enabled.control.checked;
      const copySource = enabled.control.checked && (create || mode.control.value === "copy");
      const selected = discovered.repositories.find(item => item.fullName === repositories.control.value);
      if (enabled.control.checked && !connected) throw new Error("Connect GitHub first.");
      if (enabled.control.checked && !create && !selected) throw new Error("Select a repository.");
      if (create && !owner.control.value) throw new Error("Select a GitHub account.");
      const destination = create ? `${owner.control.value}/${repo.control.value}` : selected?.fullName;
      const confirmation = create
        ? `Create private repository ${destination} and copy the portal source into it? No GitHub workflows are installed.`
        : copySource
          ? `Copy the portal source into ${destination} on branch ${selected.defaultBranch || "main"}? Only an empty private repository or one containing the matching portal LICENSE is accepted. Existing code will not be overwritten. No GitHub workflows are installed.`
          : "Save the GitHub connection without copying source files?";
      if (!confirm(confirmation)) { status.textContent = "Cancelled."; return; }
      saved = await request("/github", "PUT", {
        enabled: enabled.control.checked, owner: create ? owner.control.value : selected?.owner || saved.owner || "organization",
        repository: create ? repo.control.value : selected?.name || saved.repository || "portal",
        branch: create ? "main" : selected?.defaultBranch || saved.branch || "main", create, seed: copySource, confirm: true,
      });
      if (create && saved.enabled) {
        const fullName = `${saved.owner}/${saved.repository}`;
        discovered.repositories.push({ fullName, owner: saved.owner, name: saved.repository, defaultBranch: saved.branch, private: true });
        repositories.control.appendChild(el("option", { value: fullName, text: fullName }));
        repositories.control.value = fullName;
      }
      if (saved.enabled) mode.control.value = "connect";
      settings.github = saved; toggle();
      showRunnerProbe();
      status.textContent = !saved.enabled ? "GitHub disabled. Saved credentials are retained; revoke access in GitHub when no longer needed."
        : copySource && saved.seeded ? `Portal source verified in ${saved.owner}/${saved.repository} on branch ${saved.branch}. Repository connected.`
          : "Repository connection saved. No source files were copied.";
    }, status);
    const sync = button("Sync catalog now", async () => {
      const result = await request("/github/sync", "POST", {});
      status.textContent = result.detail || "Processing queued.";
      await refreshJob();
    }, status);
    const workflows = button("Create workflow PR", async () => {
      if (!confirm(`Propose the organization build and capacity workflows in ${saved.owner}/${saved.repository}? Existing helper changes will be visible for review. Nothing is merged, deployed, or granted Azure access.`)) { status.textContent = "Cancelled."; return; }
      const result = await request("/github/workflows", "POST", { confirm: true });
      if (result.status === "current") { status.textContent = "Workflow files are already current."; return; }
      const url = new URL(result.url);
      if (url.origin !== "https://github.com" || !url.pathname.startsWith(`/${saved.owner}/${saved.repository}/pull/`)) throw new Error("Unexpected workflow review URL.");
      status.replaceChildren(document.createTextNode("Workflow changes are ready for review. "), el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", text: "Review pull request" }));
    }, status);

    function toggle() {
      const on = enabled.control.checked;
      connect.hidden = advanced.hidden = !on || (connected && !!saved.enabled);
      connect.disabled = polling || !settings.githubDeviceConfigured || !!saved.enabled;
      connect.textContent = polling ? "Checking sign-in..." : flowId ? "Check sign-in" : connected ? "Reconnect GitHub" : "Connect GitHub";
      cancel.hidden = !on || !flowId;
      cancel.disabled = polling;
      vault.node.hidden = vaultSecret.node.hidden = storage.control.value !== "key_vault";
      selection.hidden = !on || !connected;
      repositories.node.hidden = mode.control.value === "create";
      owner.node.hidden = repo.node.hidden = mode.control.value !== "create";
      save.disabled = on && !connected;
      save.textContent = on && mode.control.value === "copy" ? "Copy portal source" : "Save GitHub configuration";
      sync.hidden = !on || !saved.enabled;
      workflows.hidden = !on || !saved.enabled || processing.mode === "container";
      processingSection.hidden = !on || !saved.enabled;
      if (!on) { clearTimeout(timer); device.hidden = true; }
    }
    storage.control.addEventListener("change", toggle);
    mode.control.addEventListener("change", toggle);
    enabled.control.addEventListener("change", () => { toggle(); if (enabled.control.checked && flowId) poll(); });
    body.append(el("h3", { text: "Repository connection" }), enabled.node, account);
    body.appendChild(oauthSettings);
    body.append(advanced, el("div", { class: "setup-actions" }, [connect, cancel]), device, selection, el("div", { class: "setup-actions" }, [save, sync, workflows]));
    body.append(processingSection);
    showRunnerProbe();
    toggle();
    if (["queued", "running"].includes(settings.processingJob?.status)) jobTimer = setTimeout(refreshJob, 1000);
    if (connected) discover().catch(error => { connected = false; account.textContent = error.message; toggle(); });
    if (settings.syncError) body.appendChild(el("p", { class: "setup-status", text: settings.syncError }));
  }

  function subscriptions(body, settings, status) {
    let entries = (settings.subscriptions || []).map(item => ({ ...item }));
    let busy = false;
    const scope = el("div", { class: "setup-subscriptions" });
    const tabs = body.parentElement.querySelectorAll('[role="tab"]');
    const identifier = field("Subscription ID");
    identifier.control.placeholder = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx";
    identifier.control.autocomplete = "off";
    identifier.control.spellcheck = false;
    const pricing = choice("Pricing preference", [["retail", "Public retail"], ["negotiated", "Negotiated"]], "retail");
    pricing.control.title = "Negotiated prices fall back to public retail when unavailable; estimates state the reason.";
    const count = el("span", { class: "muted" });
    const add = el("button", { type: "submit", class: "deploy-btn", text: "Add and save" });
    const form = el("form", { class: "setup-subscription-add", "aria-label": "Add subscription" }, [identifier.node, pricing.node, add]);
    const table = el("div", { class: "setup-table setup-subscription-list" });
    const csv = field("Subscriptions CSV", "", "file");
    csv.control.accept = ".csv,text/csv";
    const importButton = el("button", { type: "button", class: "deploy-btn-secondary", text: "Import and save" });
    const imports = el("details", { class: "setup-subscription-import" }, [
      el("summary", { text: "Import CSV" }),
      el("div", { class: "setup-subscription-import-row" }, [csv.node, importButton]),
    ]);

    function toggle() {
      scope.setAttribute("aria-busy", String(busy));
      identifier.control.disabled = pricing.control.disabled = busy || entries.length >= 200;
      add.disabled = busy || entries.length >= 200 || !identifier.control.value.trim();
      csv.control.disabled = busy;
      importButton.disabled = busy || !csv.control.files.length;
      table.querySelectorAll("select").forEach(control => { control.disabled = busy; });
      tabs.forEach(tab => { tab.disabled = busy; });
    }

    async function save(next) {
      const result = await request("/subscriptions", "PUT", {
        subscriptions: next.map(item => ({ subscriptionId: item.subscriptionId, pricingMode: item.pricingMode })),
      });
      entries = result.subscriptions;
      settings.subscriptions = entries;
      document.dispatchEvent(new CustomEvent("techlabs:setup-saved"));
    }

    async function perform(message, action, focusLabel) {
      if (busy) return;
      busy = true;
      status.textContent = message;
      toggle();
      try { await action(); }
      catch (error) { status.textContent = error.message; }
      finally {
        busy = false;
        draw();
        toggle();
        if (scope.isConnected && focusLabel) {
          [...scope.querySelectorAll("input,select")].find(control => control.getAttribute("aria-label") === focusLabel)?.focus();
        }
      }
    }

    function draw() {
      count.textContent = `${entries.length} saved`;
      table.replaceChildren();
      if (!entries.length) {
        table.appendChild(el("p", { class: "muted setup-subscription-empty", text: "No subscriptions yet." }));
        return;
      }
      const rows = el("tbody");
      entries.forEach(item => {
        const mode = choice(`Pricing for ${item.displayName}`, [["retail", "Public retail"], ["negotiated", "Negotiated"]], item.pricingMode);
        mode.control.title = pricing.control.title;
        mode.control.addEventListener("change", () => {
          const selectedMode = mode.control.value;
          if (selectedMode === item.pricingMode) return;
          perform("Saving pricing...", async () => {
            await save(entries.map(entry => entry.subscriptionId === item.subscriptionId ? { ...entry, pricingMode: selectedMode } : entry));
            status.textContent = "Pricing saved.";
          }, `Pricing for ${item.displayName}`);
        });
        rows.appendChild(el("tr", {}, [
          el("td", {}, [el("strong", { text: item.displayName }), el("div", { class: "muted setup-subscription-id", text: item.subscriptionId })]),
          el("td", {}, mode.control),
        ]));
      });
      table.appendChild(el("table", { class: "admin-table", "aria-label": "Saved subscriptions" }, [el("thead", {}, el("tr", {}, ["Subscription", "Pricing"].map(text => el("th", { text })))), rows]));
    }

    identifier.control.addEventListener("input", toggle);
    form.addEventListener("submit", event => {
      event.preventDefault();
      perform("Verifying and saving subscription...", async () => {
        const item = { subscriptionId: identifier.control.value.trim().toLowerCase(), pricingMode: pricing.control.value };
        if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item.subscriptionId) || item.subscriptionId === "00000000-0000-0000-0000-000000000000") throw new Error("Enter a nonzero subscription GUID.");
        if (entries.some(entry => entry.subscriptionId === item.subscriptionId)) throw new Error("Subscription already saved. Change its pricing in the list.");
        if (entries.length >= 200) throw new Error("A maximum of 200 subscriptions can be saved.");
        await save([...entries, item]);
        identifier.control.value = "";
        status.textContent = "Subscription verified and saved.";
      }, "Subscription ID");
    });
    csv.control.addEventListener("change", toggle);
    importButton.addEventListener("click", () => {
      perform("Verifying and saving CSV subscriptions...", async () => {
        const file = csv.control.files[0];
        if (!file) throw new Error("Choose a subscriptions CSV first.");
        if (file.size > 131072) throw new Error("CSV must not exceed 128 KiB.");
        const result = await request("/subscriptions/parse", "POST", { csv: await file.text() });
        const merged = new Map(entries.map(item => [item.subscriptionId, item]));
        result.subscriptions.forEach(item => merged.set(item.subscriptionId, item));
        if (merged.size > 200) throw new Error("A maximum of 200 subscriptions can be saved.");
        await save([...merged.values()]);
        csv.control.value = "";
        imports.open = false;
        status.textContent = "CSV subscriptions verified and saved.";
      });
    });

    scope.append(el("div", { class: "setup-subscription-heading" }, [el("h3", { text: "Subscriptions" }), count]), form, table, imports);
    body.appendChild(scope);
    draw();
    toggle();
  }

  trigger.addEventListener("click", open);
  document.addEventListener("techlabs:me", event => {
    trigger.hidden = !event.detail?.isAdmin;
    if (event.detail?.isAdmin && event.detail?.setupRequired) open();
  });
})();