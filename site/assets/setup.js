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
      control.setAttribute("data-setup-busy", "true");
      status.textContent = "Working...";
      try { await action(); } catch (error) { status.textContent = error.message; }
      finally { control.disabled = false; control.removeAttribute("data-setup-busy"); }
    });
    return control;
  }

  async function open() {
    panel.open((body) => {
      const wizard = el("div", { class: "setup-wizard" });
      const heading = el("h2", { text: "Portal setup" });
      const loading = el("p", { class: "setup-status", role: "status", text: "Loading configuration..." });
      const tabs = el("div", { class: "tabs setup-tabs", role: "tablist", "aria-label": "Setup steps" });
      const views = el("div", { class: "setup-views" });
      const progress = el("span", { class: "setup-progress" });
      const guidance = el("p", { class: "setup-next-action", role: "status" });
      const previous = el("button", { type: "button", class: "deploy-btn-secondary", text: "Back" });
      const next = el("button", { type: "button", class: "deploy-btn", text: "Next" });
      const navigation = el("div", { class: "setup-navigation", hidden: true }, [
        el("div", {}, [progress, guidance]), el("div", { class: "setup-navigation-buttons" }, [previous, next]),
        el("p", { class: "setup-navigation-note", text: "Back and Next keep your draft on screen; they do not save it. Use the Save actions before closing setup." }),
      ]);
      wizard.append(heading, loading, tabs, views, navigation);
      body.appendChild(wizard);
      request("").then((settings) => {
        if (!wizard.isConnected) return;
        loading.remove();
        const builders = [
          ["Authentication", authentication], ["GitHub", github],
          ["Subscriptions", subscriptions], ["Branding", branding],
        ];
        const pages = [];
        const indicators = [];
        let selected = 0;
        function updateNavigation() {
          const busy = !!views.querySelector('[data-setup-busy="true"], [aria-busy="true"]');
          previous.disabled = busy || selected === 0;
          next.disabled = busy;
          tabs.querySelectorAll("button").forEach(tab => { tab.disabled = busy; });
          next.textContent = selected === builders.length - 1 ? "Close setup" : `Next: ${builders[selected + 1][0]}`;
          progress.textContent = `Step ${selected + 1} of ${builders.length}`;
          const savedStates = [settings.primaryAuthentication?.verified ? "Verified" : settings.primaryAuthentication?.clientId ? "Configured" : "Needs attention",
            settings.github?.enabled ? "Repository saved" : "Optional",
            settings.subscriptions?.length ? `${settings.subscriptions.length} saved` : "Not added", "Optional"];
          indicators.forEach((node, index) => { node.textContent = savedStates[index]; });
          const messages = [
            settings.primaryAuthentication?.clientId ? "Company sign-in is configured. Next: connect your GitHub account and repository, or skip the optional integration."
              : "Verify company sign-in with the host administrator before configuring repository access.",
            pages[1]?.content.dataset.nextAction || "Connect a repository for reviewed uploads, or continue without GitHub.",
            settings.subscriptions?.length ? "Subscription access is saved. Next: review the site name and footer."
              : "Add a subscription ID and save to verify access. Next: review branding, or return here before deploying workloads.",
            "Save any branding changes, then close setup. Catalog processing and pending work are available in Admin > Operations.",
          ];
          guidance.textContent = busy ? "An operation is in progress. Complete or cancel it before changing steps." : messages[selected];
        }
        function show(index, focus = true) {
          if (views.querySelector('[data-setup-busy="true"], [aria-busy="true"]')) return;
          selected = index;
          pages.forEach((page, pageIndex) => { page.section.hidden = pageIndex !== index; });
          tabs.querySelectorAll("button").forEach((tab, tabIndex) => {
            tab.setAttribute("aria-selected", String(tabIndex === index));
            tab.tabIndex = tabIndex === index ? 0 : -1;
          });
          const page = pages[index];
          if (!page.built) {
            page.built = true;
            builders[index][1](page.content, settings, page.status);
          }
          updateNavigation();
          if (focus) tabs.children[index].focus();
        }
        builders.forEach(([title], index) => {
          const indicator = el("span", { class: "setup-step-state" });
          indicators.push(indicator);
          const tab = el("button", { id: `setup-tab-${index}`, type: "button", role: "tab", class: "tab", "aria-controls": `setup-step-${index}` }, [
            el("span", { class: "setup-step-number", text: String(index + 1), "aria-hidden": "true" }), el("span", {}, [el("span", { text: title }), indicator]),
          ]);
          const content = el("div", { class: "setup-content" });
          const status = el("p", { class: "setup-status setup-step-status", role: "status" });
          const section = el("section", { id: `setup-step-${index}`, class: "setup-step", role: "tabpanel", "aria-labelledby": `setup-tab-${index}`, hidden: true }, [content, status]);
          pages.push({ section, content, status, built: false });
          views.appendChild(section);
          tab.addEventListener("click", () => show(index));
          tab.addEventListener("keydown", event => {
            const destination = { ArrowRight: (index + 1) % builders.length, ArrowLeft: (index + builders.length - 1) % builders.length, Home: 0, End: builders.length - 1 }[event.key];
            if (destination === undefined) return;
            event.preventDefault();
            show(destination);
          });
          tabs.appendChild(tab);
        });
        previous.addEventListener("click", () => show(Math.max(0, selected - 1)));
        next.addEventListener("click", () => {
          if (selected === builders.length - 1) panel.close();
          else show(selected + 1);
        });
        new MutationObserver(updateNavigation).observe(views, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["data-setup-busy", "aria-busy", "data-next-action"] });
        navigation.hidden = false;
        show(0, false);
      }).catch(error => { loading.textContent = error.message; });
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
    body.append(el("div", { class: "setup-branding" }, [el("h3", { text: "Site branding" }),
      el("div", { class: "setup-grid setup-branding-fields" }, [name.node, footer.node]), el("div", { class: "setup-actions" }, [save])]));
  }

  function authentication(body, settings, status) {
    const primary = settings.primaryAuthentication || {};
    body.appendChild(el("h3", { text: "Primary Entra connection" }));
    body.appendChild(el("p", { class: "setup-connection-state", text: primary.verified ? "Sign-in verified" : primary.clientId ? "Configured in App Service" : "Primary connection not configured" }));
    body.appendChild(el("div", { class: "setup-grid setup-primary-facts" }, [
      el("div", {}, [el("span", { class: "muted small", text: "Tenant ID" }), el("div", { class: "setup-identifier", text: settings.azureTenantId || "Not available" })]),
      el("div", {}, [el("span", { class: "muted small", text: "Application ID" }), el("div", { class: "setup-identifier", text: primary.clientId || "Not configured" })]),
    ]));
    const additional = el("details", { class: "setup-advanced" }, [el("summary", { text: "Additional identity provider (optional)" })]);
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
    let applicationReady = !!settings.githubDeviceConfigured;
    let connected = false;
    let repositoryReady = !!saved.enabled;
    let savingApplication = false;
    let savingRepository = false;
    let discovering = false;
    let discovered = { owners: [], repositories: [] };
    let flowId = "";
    let timer = null;
    let polling = false;
    let processing = settings.processing || { mode: "container", runnerLabels: [], capacityRegions: [] };
    let runnerProbe = settings.runnerProbe || {};
    let jobTimer = null;
    const enabled = field("Enable GitHub", "", "checkbox");
    enabled.node.classList.add("setup-inline-toggle");
    enabled.control.checked = !!saved.enabled;
    const mode = choice("Repository action", [["connect", "Connect existing repository"], ["create", "Create private repository + source"], ["copy", "Copy source to existing repository"]], "connect");
    const owner = choice("GitHub account", [], "");
    const repositories = choice("Repository", [], "");
    const repo = field("New repository name", "deployment-portal");
    const account = el("p", { class: "muted", text: settings.githubRenewalError || (connected
      ? `GitHub connection available.${settings.githubConnection?.autoRenew ? " Automatic renewal enabled." : ""}` : "Not connected to GitHub.") });
    const device = el("div", { class: "setup-status", hidden: true });
    const oauthClient = field("GitHub OAuth App client ID", settings.githubOAuthClientId || "");
    const oauthSave = button("Save OAuth application", async () => {
      if (!confirm("Use this organization-owned GitHub OAuth App for device sign-in?")) { status.textContent = "Cancelled."; return; }
      applicationReady = connected = repositoryReady = false;
      savingApplication = true;
      toggle();
      try {
        const result = await request("/github/oauth-client", "PUT", { clientId: oauthClient.control.value.trim(), confirm: true });
        settings.githubOAuthClientId = result.clientId;
        settings.githubDeviceConfigured = result.configured;
        applicationReady = result.configured === true;
        account.textContent = "Authorize the account for the saved OAuth application.";
        status.textContent = applicationReady ? "Step 1 saved. Continue to step 2: authorize your GitHub account." : "OAuth application is not configured. Step 2 remains locked.";
      } finally { savingApplication = false; toggle(); }
    }, status);
    const oauthSettings = el("details", { class: "setup-advanced", open: !settings.githubDeviceConfigured }, [
      el("summary", { text: "GitHub OAuth application" }),
      el("p", { class: "muted small", text: "Use your organization's OAuth App with Device Flow enabled. A client ID is not a client secret." }), oauthClient.node,
      el("div", { class: "setup-actions" }, [oauthSave, el("a", { href: "https://github.com/settings/developers", target: "_blank", rel: "noopener noreferrer", text: "GitHub developer settings" })]),
    ]);
    const credential = settings.githubCredentialStorage || {};
    const storage = choice("GitHub token storage", [["app_service", "App Service settings"], ["key_vault", "Azure Key Vault"]], credential.secretStorage || "app_service");
    const vault = field("GitHub Key Vault resource ID", credential.keyVaultResourceId || "");
    const vaultSecret = field("GitHub Key Vault secret name", credential.keyVaultSecretName || "github-token");
    const advanced = el("details", { class: "setup-advanced" }, [el("summary", { text: "Token storage (advanced)" }), el("div", { class: "setup-grid" }, [storage.node, vault.node, vaultSecret.node])]);
    mode.node.classList.add("setup-full-width");
    repositories.node.classList.add("setup-full-width");
    const selection = el("div", { class: "setup-grid" }, [mode.node, owner.node, repo.node, repositories.node]);
    const destinationStatus = el("p", { class: "setup-destination-status muted small" });
    const accountGroup = el("section", { class: "setup-github-group" });
    const repositoryGroup = el("section", { class: "setup-github-group" });
    const connectionGroups = el("div", { class: "setup-github-columns" }, [accountGroup, repositoryGroup]);
    function step(title) {
      const state = el("p", { class: "setup-github-step-state" });
      const content = el("div", { class: "setup-github-step-content" });
      const group = el("fieldset", { class: "setup-github-step" }, [el("legend", { text: title }), state, content]);
      return { group, state, content };
    }
    const applicationStep = step("1. Save OAuth application");
    const authorizationStep = step("2. Authorize GitHub account");
    const repositoryStep = step("3. Save repository connection");
    const processingStep = step("4. Catalog processing (optional)");
    function gate(target, locked, message) {
      target.group.disabled = locked;
      target.group.setAttribute("aria-disabled", String(locked));
      target.content.inert = locked;
      target.state.textContent = message;
    }
    const processingMode = choice("Catalog processing", [["container", "Portal container"], ["github-hosted", "GitHub-hosted runners"], ["self-hosted", "Self-hosted runners"]], processing.mode);
    const runnerLabels = choice("Verified Linux runner", [], "");
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
      if (!confirm("Save the processing location? Quota refresh discovers all regions for saved subscriptions. Unavailable runners fall back to the portal container. Existing workflows are unchanged.")) return;
      processing = await request("/github/processing", "PUT", {
        mode: processingMode.control.value,
        runnerLabels: processingMode.control.value === "self-hosted" ? JSON.parse(runnerLabels.control.value || "[]") : [],
        capacityRegions: [], confirm: true,
      });
      settings.processing = processing;
      status.textContent = "Processing settings saved.";
      toggle();
      await refreshJob();
    }, status);
    processingMode.control.addEventListener("change", () => { runnerLabels.node.hidden = processingMode.control.value !== "self-hosted"; });
    const processingSummary = el("summary", { text: "Catalog processing (optional)" });
    const processingSection = el("details", { class: "setup-processing setup-advanced" }, [processingSummary, el("div", { class: "setup-grid" }, [processingMode.node, runnerLabels.node]),
      el("p", { class: "muted small", text: "Region scope: all Azure regions for each saved subscription." }), runnerStatus,
      el("div", { class: "setup-actions" }, [checkRunners, saveProcessing]), jobStatus]);

    async function discover() {
      connected = false;
      discovering = true;
      toggle();
      try {
      discovered = await request("/github/discover");
      account.textContent = `Connected as ${discovered.account.login}.${settings.githubConnection?.autoRenew ? " Automatic renewal enabled." : ""}`;
      owner.control.replaceChildren(...discovered.owners.map(item => el("option", { value: item.login, text: item.login })));
      repositories.control.replaceChildren(el("option", { value: "", text: "Select repository" }), ...discovered.repositories.map(item => el("option", { value: item.fullName, text: item.fullName })));
      if (saved.owner) owner.control.value = saved.owner;
      if (saved.repository) repositories.control.value = `${saved.owner}/${saved.repository}`;
      connected = true;
      repositoryReady = saved.enabled === true && discovered.repositories.some(item => item.fullName === `${saved.owner}/${saved.repository}`);
      if (saved.enabled && (runnerProbe.repository !== `${saved.owner}/${saved.repository}` || Date.now() / 1000 - (runnerProbe.checkedAt || 0) > 600)) {
        request("/github/runners/probe", "POST", {}).then(result => { runnerProbe = result; showRunnerProbe(); })
          .catch(error => { runnerStatus.textContent = `${error.message} Portal container remains available.`; });
      }
      status.textContent = discovered.truncated ? "Showing the first 500 accessible repositories/organizations; narrow access in GitHub if the target is missing." : "Account and repositories loaded from GitHub.";
      } finally { discovering = false; toggle(); }
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
          settings.githubConnection = { ...settings.githubConnection, autoRenew: result.autoRenew === true };
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
      if (!applicationReady) throw new Error("Complete step 1 before authorizing GitHub.");
      const destination = storage.control.value === "key_vault" ? "the selected Key Vault" : "encrypted App Service settings";
      const renewal = saved.enabled ? "Use the previously connected GitHub account. The saved repository and branch will stay unchanged. " : "";
      if (!confirm(`Authorize GitHub repository, organization and workflow-file access? ${renewal}Access and renewal credentials will be stored server-side in ${destination} for background sync and reviewed workflow installation. When GitHub supplies a refresh token, the portal will renew access automatically. Host settings may restart the app.`)) { status.textContent = "Cancelled."; return; }
      connected = repositoryReady = false;
      polling = true;
      toggle();
      try {
      const result = await request("/github/device/start", "POST", {
        secretStorage: storage.control.value, secretSettingName: "GALLERY_GITHUB_TOKEN",
        keyVaultResourceId: storage.control.value === "key_vault" ? vault.control.value : "",
        keyVaultSecretName: storage.control.value === "key_vault" ? vaultSecret.control.value : "", confirm: true,
      });
      flowId = result.flowId;
      device.replaceChildren(el("p", { text: "GitHub verification code" }), el("strong", { class: "setup-device-code", text: result.userCode }), el("p", {}, el("a", { href: "https://github.com/login/device", target: "_blank", rel: "noopener noreferrer", text: "Authorize on GitHub" })));
      device.hidden = false;
      status.textContent = "Waiting for GitHub authorization...";
      timer = setTimeout(poll, Math.max(1, result.interval) * 1000);
      } finally { polling = false; toggle(); }
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
      if (enabled.control.checked && (!applicationReady || !connected)) throw new Error("Complete steps 1 and 2 before saving a repository.");
      if (enabled.control.checked && !create && !selected) throw new Error("Select a repository.");
      if (create && !owner.control.value) throw new Error("Select a GitHub account.");
      const destination = create ? `${owner.control.value}/${repo.control.value}` : selected?.fullName;
      const confirmation = create
        ? `Create private repository ${destination} and copy the portal source into it? No GitHub workflows are installed.`
        : copySource
          ? `Copy the portal source into ${destination} on branch ${selected.defaultBranch || "main"}? Only an empty private repository or one containing the matching portal LICENSE is accepted. Existing code will not be overwritten. No GitHub workflows are installed.`
          : "Save the GitHub connection without copying source files?";
      if (!confirm(confirmation)) { status.textContent = "Cancelled."; return; }
      repositoryReady = false;
      savingRepository = true;
      toggle();
      try {
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
      repositoryReady = saved.enabled === true;
      settings.github = saved; toggle();
      showRunnerProbe();
      status.textContent = !saved.enabled ? "GitHub disabled. Saved credentials are retained; revoke access in GitHub when no longer needed."
        : copySource && saved.seeded ? `Portal source verified in ${saved.owner}/${saved.repository} on branch ${saved.branch}. Repository connected.`
          : "Repository connection saved. No source files were copied.";
          } finally { savingRepository = false; toggle(); }
    }, status);
    const sync = button("Sync catalog now", async () => {
      const result = await request("/github/sync", "POST", {});
      status.textContent = result.detail || "Processing queued.";
      await refreshJob();
    }, status);
    const workflows = button("Create workflow PR", async () => {
      if (!confirm(`Propose the organization build, capacity and roadmap workflows in ${saved.owner}/${saved.repository}? Existing helper changes will be visible for review. Nothing is merged, deployed, or granted Azure access.`)) { status.textContent = "Cancelled."; return; }
      const result = await request("/github/workflows", "POST", { confirm: true });
      if (result.status === "current") { status.textContent = "Workflow files are already current."; return; }
      const url = new URL(result.url);
      if (url.origin !== "https://github.com" || !url.pathname.startsWith(`/${saved.owner}/${saved.repository}/pull/`)) throw new Error("Unexpected workflow review URL.");
      status.replaceChildren(document.createTextNode("Workflow changes are ready for review. "), el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", text: "Review pull request" }));
    }, status);
    const repositoryActions = el("div", { class: "setup-actions" }, [save]);
    const disabledActions = el("div", { class: "setup-actions" });

    function toggle() {
      const on = enabled.control.checked;
      const busy = savingApplication || savingRepository || discovering || polling;
      const authorizationPending = polling || !!flowId;
      const accountReady = applicationReady && connected && !authorizationPending;
      const destinationReady = accountReady && repositoryReady && saved.enabled && mode.control.value === "connect" && repositories.control.value === `${saved.owner}/${saved.repository}`;
      enabled.control.disabled = busy || !!flowId;
      body.setAttribute("aria-busy", String(busy || !!flowId));
      gate(applicationStep, !on || busy || !!flowId, applicationReady ? "Saved" : "Save the OAuth App client ID to unlock step 2.");
      gate(authorizationStep, !on || !applicationReady || savingApplication || savingRepository || discovering,
        !applicationReady ? "Locked until step 1 succeeds." : discovering ? "Verifying account access..." : accountReady ? "Account verified" : "Authorize the account to unlock step 3.");
      gate(repositoryStep, !on || !accountReady || busy, !accountReady ? "Locked until step 2 succeeds."
        : destinationReady ? "Repository saved" : "Save the selected repository to unlock step 4.");
      gate(processingStep, !on || !destinationReady || busy, destinationReady ? "Ready (optional)" : "Locked until step 3 succeeds.");
      body.dataset.nextAction = !on ? saved.enabled
        ? "GitHub is still enabled in the saved configuration. Save GitHub as disabled to apply this change, or re-enable the checkbox."
        : "GitHub is optional. Enable it for repository-backed reviews, or continue to Subscriptions."
        : !applicationReady ? "Step 1: save the client ID of your organization-owned GitHub OAuth App, with Device Flow enabled."
          : !connected ? saved.enabled ? "Next: reconnect the same GitHub account. The saved repository and branch remain selected."
            : "Next: Connect GitHub and authorize the code in your browser. No repository is selected yet."
            : mode.control.value === "create" ? "Next: review the account and new name, then Create repository + copy source. Creation requires your confirmation."
              : mode.control.value === "copy" ? "Next: select an empty or matching LICENSE-only repository, then Copy portal source. Existing code will not be overwritten."
            : !saved.enabled || !repositoryReady ? "Step 3: select a repository and save successfully to unlock catalog processing."
              : repositories.control.value !== `${saved.owner}/${saved.repository}` ? "Next: save the selected repository connection. The previously saved repository remains active until you save."
              : `Saved repository: ${saved.owner}/${saved.repository}. Next: add deployment subscriptions. Catalog processing is optional here.`;
      connect.hidden = advanced.hidden = !on;
      connect.disabled = polling || discovering || !applicationReady;
      connect.textContent = polling ? "Checking sign-in..." : flowId ? "Check sign-in" : connected ? "Reconnect GitHub" : "Connect GitHub";
      cancel.hidden = !on || !flowId;
      cancel.disabled = polling;
      vault.node.hidden = vaultSecret.node.hidden = storage.control.value !== "key_vault";
      selection.hidden = !on;
      connectionGroups.hidden = !on;
      destinationStatus.textContent = !connected ? "Connect your GitHub account first to load accessible repositories."
        : saved.enabled ? `Saved: ${saved.owner}/${saved.repository} / Branch: ${saved.branch}`
          : "No repository saved. Choose a destination, then save the connection.";
      repositories.node.hidden = mode.control.value === "create";
      owner.node.hidden = repo.node.hidden = mode.control.value !== "create";
      save.disabled = savingRepository || (on && !accountReady);
      save.textContent = !on ? "Save GitHub as disabled" : mode.control.value === "copy" ? "Copy portal source"
        : mode.control.value === "create" ? "Create repository + copy source" : "Save GitHub configuration";
      disabledActions.hidden = on || !saved.enabled;
      (on ? repositoryActions : disabledActions).appendChild(save);
      sync.hidden = !on;
      workflows.hidden = !on || !saved.enabled || processing.mode === "container";
      processingStep.group.hidden = !on;
      processingSummary.textContent = `Catalog processing (optional) / ${processing.mode === "container" ? "Portal container" : processing.mode === "self-hosted" ? "Self-hosted runners" : "GitHub-hosted runners"}`;
      if (!on) { clearTimeout(timer); device.hidden = true; }
    }
    storage.control.addEventListener("change", toggle);
    oauthClient.control.addEventListener("input", () => { applicationReady = connected = repositoryReady = false; toggle(); });
    function repositoryChanged() { repositoryReady = false; toggle(); }
    mode.control.addEventListener("change", repositoryChanged);
    repositories.control.addEventListener("change", repositoryChanged);
    owner.control.addEventListener("change", repositoryChanged);
    repo.control.addEventListener("input", repositoryChanged);
    enabled.control.addEventListener("change", () => { toggle(); if (enabled.control.checked && flowId) poll(); });
    applicationStep.content.appendChild(oauthSettings);
    authorizationStep.content.append(account, advanced, el("div", { class: "setup-actions" }, [connect, cancel]), device);
    accountGroup.append(applicationStep.group, authorizationStep.group);
    repositoryStep.content.append(destinationStatus, selection, repositoryActions);
    repositoryGroup.appendChild(repositoryStep.group);
    body.append(el("div", { class: "setup-section-heading" }, [el("h3", { text: "Repository connection" }), enabled.node]), connectionGroups, disabledActions);
    processingSection.appendChild(el("div", { class: "setup-actions" }, [sync, workflows]));
    processingStep.content.appendChild(processingSection);
    body.append(processingStep.group);
    showRunnerProbe();
    toggle();
    if (["queued", "running"].includes(settings.processingJob?.status)) jobTimer = setTimeout(refreshJob, 1000);
    if (settings.githubConnected && applicationReady) discover().catch(error => { connected = false; account.textContent = error.message; toggle(); });
    if (settings.syncError) body.appendChild(el("p", { class: "setup-status", text: settings.syncError }));
  }

  function subscriptions(body, settings, status) {
    let entries = (settings.subscriptions || []).map(item => ({ ...item }));
    let busy = false;
    const scope = el("div", { class: "setup-subscriptions" });
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