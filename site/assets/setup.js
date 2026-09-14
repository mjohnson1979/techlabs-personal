(function () {
  "use strict";
  const el = window.TechlabsDom.el;
  const panel = window.TechlabsPanel;
  const trigger = document.getElementById("nav-setup");

  async function request(path, method = "GET", data) {
    const response = await fetch("/api/admin/setup" + path, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the setup fields and try again.");
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
    const name = field("Provider name", saved.providerName || "customer");
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
          ? "Existing customer vault required. The host's Key Vault reference identity must be the setup managed identity with secret access. App Service stores a reference only."
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
      const target = storage.control.value === "key_vault" ? "the selected customer Key Vault, with only a reference in App Service" : "an encrypted App Service setting";
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
    const enabled = field("Enable GitHub", "", "checkbox");
    enabled.control.checked = !!saved.enabled;
    const mode = choice("Repository action", [["connect", "Use an existing repository"], ["create", "Create a private repository"]], "connect");
    const owner = choice("GitHub account", [], "");
    const repositories = choice("Repository", [], "");
    const repo = field("New repository name", "deployment-portal");
    const account = el("p", { class: "muted", text: connected ? "GitHub connection available." : "Not connected to GitHub." });
    const device = el("div", { class: "setup-status", hidden: true });
    const oauthClient = field("GitHub OAuth App client ID", settings.githubOAuthClientId || "");
    const oauthSave = button("Save OAuth application", async () => {
      if (!confirm("Use this customer-owned GitHub OAuth App for device sign-in?")) { status.textContent = "Cancelled."; return; }
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

    async function discover() {
      discovered = await request("/github/discover");
      connected = true;
      account.textContent = `Connected as ${discovered.account.login}.`;
      owner.control.replaceChildren(...discovered.owners.map(item => el("option", { value: item.login, text: item.login })));
      repositories.control.replaceChildren(el("option", { value: "", text: "Select repository" }), ...discovered.repositories.map(item => el("option", { value: item.fullName, text: item.fullName })));
      if (saved.owner) owner.control.value = saved.owner;
      if (saved.repository) repositories.control.value = `${saved.owner}/${saved.repository}`;
      status.textContent = discovered.truncated ? "Showing the first 500 accessible repositories/organizations; narrow access in GitHub if the target is missing." : "Account and repositories loaded from GitHub.";
      toggle();
    }

    async function poll() {
      if (!body.isConnected || !enabled.control.checked || !flowId || polling) return;
      clearTimeout(timer);
      polling = true;
      try {
        const result = await request("/github/device/poll", "POST", { flowId });
        if (result.status === "pending") {
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
        status.textContent = `${error.message} Reopen setup after any host restart, or check sign-in again.`;
        toggle();
      } finally { polling = false; }
    }

    const connect = button("Connect GitHub", async () => {
      if (flowId) { await poll(); return; }
      const destination = storage.control.value === "key_vault" ? "the selected Key Vault" : "encrypted App Service settings";
      if (!confirm(`Authorize GitHub repository and organization access? The token will be stored server-side in ${destination} for background sync. Host settings may restart the app.`)) { status.textContent = "Cancelled."; return; }
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
      const selected = discovered.repositories.find(item => item.fullName === repositories.control.value);
      if (enabled.control.checked && !connected) throw new Error("Connect GitHub first.");
      if (enabled.control.checked && !create && !selected) throw new Error("Select a repository.");
      if (create && !owner.control.value) throw new Error("Select a GitHub account.");
      if (!confirm(create ? "Create a private repository and copy only the customer platform source into it?" : "Update the customer GitHub connection?")) { status.textContent = "Cancelled."; return; }
      saved = await request("/github", "PUT", {
        enabled: enabled.control.checked, owner: create ? owner.control.value : selected?.owner || saved.owner || "customer",
        repository: create ? repo.control.value : selected?.name || saved.repository || "portal",
        branch: create ? "main" : selected?.defaultBranch || saved.branch || "main", create, seed: create, confirm: true,
      });
      settings.github = saved; toggle();
      status.textContent = saved.enabled ? "Repository connected. Catalog sync runs every five minutes." : "GitHub disabled. Saved credentials are retained; revoke access in GitHub when no longer needed.";
    }, status);
    const sync = button("Sync catalog now", async () => {
      const result = await request("/github/sync", "POST", {});
      status.textContent = `${result.count} use cases published from commit ${result.version.slice(0, 12)}.`;
      document.dispatchEvent(new CustomEvent("techlabs:setup-saved"));
    }, status);

    function toggle() {
      const on = enabled.control.checked;
      connect.hidden = advanced.hidden = !on || (connected && !!saved.enabled);
      connect.disabled = !settings.githubDeviceConfigured || !!saved.enabled;
      connect.textContent = flowId ? "Check sign-in" : connected ? "Reconnect GitHub" : "Connect GitHub";
      cancel.hidden = !on || !flowId;
      vault.node.hidden = vaultSecret.node.hidden = storage.control.value !== "key_vault";
      selection.hidden = !on || !connected;
      repositories.node.hidden = mode.control.value === "create";
      owner.node.hidden = repo.node.hidden = mode.control.value !== "create";
      save.disabled = on && !connected;
      sync.hidden = !on || !saved.enabled;
      if (!on) { clearTimeout(timer); device.hidden = true; }
    }
    storage.control.addEventListener("change", toggle);
    mode.control.addEventListener("change", toggle);
    enabled.control.addEventListener("change", () => { toggle(); if (enabled.control.checked && flowId) poll(); });
    body.append(el("h3", { text: "Customer repository" }), enabled.node, account);
    body.appendChild(oauthSettings);
    body.append(advanced, el("div", { class: "setup-actions" }, [connect, cancel]), device, selection, el("div", { class: "setup-actions" }, [save, sync]));
    toggle();
    if (connected) discover().catch(error => { connected = false; account.textContent = error.message; toggle(); });
    if (settings.syncError) body.appendChild(el("p", { class: "setup-status", text: settings.syncError }));
  }

  function subscriptions(body, settings, status) {
    let entries = (settings.subscriptions || []).map(item => ({ ...item }));
    const identifier = field("Subscription ID");
    const pricing = choice("Pricing preference", [["retail", "Public retail"], ["negotiated", "Negotiated"]], "retail");
    pricing.control.title = "Negotiated prices fall back to public retail when unavailable; estimates state the reason.";
    body.appendChild(el("h3", { text: "Deployment subscriptions" }));
    body.appendChild(el("div", { class: "setup-grid" }, [identifier.node, pricing.node]));
    const table = el("div", { class: "setup-table" });
    function draw() {
      table.replaceChildren();
      const rows = el("tbody");
      entries.forEach(item => {
        const mode = choice(`Pricing for ${item.displayName}`, [["retail", "Public retail"], ["negotiated", "Negotiated"]], item.pricingMode);
        mode.control.addEventListener("change", () => { item.pricingMode = mode.control.value; });
        rows.appendChild(el("tr", {}, [el("td", {}, [el("strong", { text: item.displayName }), el("div", { class: "muted", text: item.subscriptionId })]), el("td", {}, mode.control)]));
      });
      table.appendChild(el("table", { class: "admin-table" }, [el("thead", {}, el("tr", {}, ["Subscription", "Pricing"].map(text => el("th", { text })))), rows]));
    }
    const csv = field("Import subscriptions CSV", "", "file");
    csv.control.accept = ".csv,text/csv";
    csv.control.addEventListener("change", async () => {
      const file = csv.control.files[0];
      if (!file) return;
      try {
        if (file.size > 131072) throw new Error("CSV must not exceed 128 KiB.");
        const result = await request("/subscriptions/parse", "POST", { csv: await file.text() });
        const merged = new Map(entries.map(item => [item.subscriptionId, item]));
        result.subscriptions.forEach(item => merged.set(item.subscriptionId, item));
        entries = [...merged.values()];
        draw();
        status.textContent = "Subscriptions resolved from Azure. Review the entries, then save.";
      } catch (error) { status.textContent = error.message; }
    });
    body.appendChild(el("div", { class: "setup-actions" }, [
      button("Add subscription", async () => {
        const item = { subscriptionId: identifier.control.value.trim().toLowerCase(), pricingMode: pricing.control.value };
        if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item.subscriptionId)) throw new Error("Enter a subscription GUID.");
        if (entries.some(entry => entry.subscriptionId === item.subscriptionId)) throw new Error("Subscription already exists. Change its pricing in the table.");
        const result = await request("/subscriptions/resolve", "POST", item);
        entries.push(result.subscription);
        identifier.control.value = "";
        draw(); status.textContent = "Subscription resolved from Azure; not saved yet.";
      }, status), csv.node,
    ]));
    body.appendChild(el("p", { class: "muted", text: "CSV: subscriptionId, with optional pricingMode (retail or negotiated)." }));
    body.appendChild(table);
    draw();
    body.appendChild(button("Verify and save subscriptions", async () => {
      const result = await request("/subscriptions", "PUT", { subscriptions: entries.map(item => ({ subscriptionId: item.subscriptionId, pricingMode: item.pricingMode })) });
      settings.subscriptions = result.subscriptions;
      entries = result.subscriptions; draw();
      status.textContent = "Subscriptions verified and saved.";
      document.dispatchEvent(new CustomEvent("techlabs:setup-saved"));
    }, status));
  }

  trigger.addEventListener("click", open);
  document.addEventListener("techlabs:me", event => {
    trigger.hidden = !event.detail?.isAdmin;
    if (event.detail?.isAdmin && event.detail?.setupRequired) open();
  });
})();