/* Admin dashboard: telemetry visuals + user management. Visible only to admins
   (the nav link is revealed by app.js when /api/me reports isAdmin). Charts are
   plain DOM bars — no external libraries, CSP-friendly. */
(function () {
  "use strict";

  const el = (window.TechlabsDom && window.TechlabsDom.el) || null;
  const Panel = window.TechlabsPanel;
  const Api = window.TechlabsApi;
  const trigger = document.getElementById("nav-admin");
  if (!el || !Panel || !Api || !trigger) return;

  function sumValues(obj) {
    return Object.keys(obj || {}).reduce((a, k) => a + obj[k], 0);
  }

  function statCard(label, value, tone) {
    return el("div", { class: "stat-card" + (tone ? " " + tone : "") }, [
      el("div", { class: "stat-value", text: String(value) }),
      el("div", { class: "stat-label", text: label }),
    ]);
  }

  function barChart(title, items, labelKey, valueKey) {
    const rows = el("div", { class: "bars" });
    const max = items.reduce((m, it) => Math.max(m, it[valueKey]), 0) || 1;
    if (!items.length) {
      rows.appendChild(el("p", { class: "muted", text: "No data yet." }));
    }
    items.forEach((it) => {
      const bar = el("meter", { class: "admin-meter", min: 0, max: max, value: it[valueKey],
        "aria-label": String(it[labelKey]) + ": " + it[valueKey] });
      rows.appendChild(
        el("div", { class: "bar-row" }, [
          el("span", { class: "bar-label", text: String(it[labelKey]) }),
          bar,
          el("span", { class: "bar-value", text: String(it[valueKey]) }),
        ])
      );
    });
    return el("div", { class: "chart" }, [el("h3", { text: title }), rows]);
  }

  function dictToItems(obj) {
    return Object.keys(obj || {})
      .map((k) => ({ k: k, n: obj[k] }))
      .sort((a, b) => b.n - a.n);
  }

  // Statuses that still hold (or are tearing down) Azure resources.
  const LIVE = ["requested", "deploying", "active", "expiring", "destroying"];
  // Statuses that can be torn down now (may hold resources). A failed deploy can
  // leave partially-created resources; an in-flight 'destroying' is excluded.
  const CLEANABLE = ["requested", "deploying", "active", "expiring", "failed"];

  function toolButton(symbol, label, onClick) {
    const button = el("button", { type: "button", class: "admin-tool", text: symbol, title: label, "aria-label": label });
    button.addEventListener("click", onClick);
    return button;
  }

  function exportCsv(filename, rows) {
    const content = rows.map((row) => row.map((value) => {
      let text = String(value == null ? "" : value);
      if (/^[\s]*[=+@-]|^[\t\r\n]/.test(text)) text = "'" + text;
      return '"' + text.replace(/"/g, '""') + '"';
    }).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", content], { type: "text/csv;charset=utf-8" }));
    const link = el("a", { href: url, download: filename });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function attentionMatch(dep, filter) {
    const end = dep.endAt ? new Date(dep.endAt).getTime() : NaN;
    if (filter === "open") return dep.status !== "destroyed";
    if (filter === "overdue") return !dep.externallyManaged && ["requested", "deploying", "active", "expiring"].includes(dep.status) && end <= Date.now();
    if (filter === "expiringSoon") return !dep.externallyManaged && ["requested", "deploying", "active"].includes(dep.status) && end > Date.now() && end <= Date.now() + 86400000;
    return filter === "all" || dep.status === filter;
  }

  function searchControl(label) {
    return el("input", { type: "search", class: "deploy-input admin-search", placeholder: label, "aria-label": label });
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleString();
  }

  // Human-friendly elapsed time between two ISO timestamps (or now).
  function fmtDuration(fromIso, toIso) {
    if (!fromIso) return "—";
    const start = new Date(fromIso).getTime();
    if (isNaN(start)) return "—";
    const end = toIso ? new Date(toIso).getTime() : Date.now();
    let secs = Math.max(0, Math.round((end - start) / 1000));
    const days = Math.floor(secs / 86400);
    secs -= days * 86400;
    const hours = Math.floor(secs / 3600);
    secs -= hours * 3600;
    const mins = Math.floor(secs / 60);
    if (days > 0) return days + "d " + hours + "h";
    if (hours > 0) return hours + "h " + mins + "m";
    return mins + "m";
  }

  function statusBadge(status) {
    const labels = { pending: "Staged locally", branched: "Review branch created", discarded: "Deleted", merged: "Merged" };
    return el("span", { class: "badge badge-" + status, text: labels[status] || status });
  }

  function deployRow(dep, onChange) {
    const owner = el("td", {}, [
      el("strong", { text: dep.userName || dep.userUpn }),
      el("div", { class: "muted small", text: dep.userUpn }),
    ]);
    const running = dep.status === "failed" ? "Unknown" : fmtDuration(dep.createdAt, dep.destroyedAt);

    const cells = [
      el("td", {}, el("strong", { text: dep.useCase })),
      el("td", {}, [el("span", { class: "chip", text: dep.environment }), el("div", { class: "muted small", text: dep.engine || "bicep" })]),
      owner,
      el("td", {}, [
        statusBadge(dep.status),
        dep.status === "failed" && dep.detail
          ? el("div", { class: "muted small", text: dep.detail, title: dep.detail })
          : null,
      ]),
      el("td", {}, [el("div", { text: fmtDate(dep.createdAt) }), el("div", { class: "muted small", text: "Elapsed: " + running })]),
      el("td", {}, [el("div", { text: dep.externallyManaged ? "External cleanup" : fmtDate(dep.endAt) }),
        attentionMatch(dep, "overdue") ? el("strong", { class: "error-text small", text: "Cleanup overdue" }) : null]),
      el("td", {}, [el("code", { text: dep.resourceGroup }), el("div", { class: "muted small", text: dep.subscriptionId })]),
    ];

    const actions = el("td", { class: "row-actions" });
    if (dep.ghRunUrl) {
      actions.appendChild(
        el("a", { href: dep.ghRunUrl, target: "_blank", rel: "noopener", class: "link-btn", text: "Portal" })
      );
    }
    // Offer cleanup for anything that may still hold resources, including a
    // failed deploy that created partial resources; an in-flight teardown is
    // already excluded from CLEANABLE.
    if (!dep.externallyManaged && CLEANABLE.indexOf(dep.status) !== -1) {
      const btn = el("button", { type: "button", class: "danger-btn", text: "Clean up" });
      btn.addEventListener("click", () => {
        const who = dep.userName || dep.userUpn;
        if (!window.confirm("Tear down " + dep.useCase + " (" + who + ") now? This deletes " + dep.resourceGroup + ".")) return;
        btn.disabled = true;
        btn.textContent = "Cleaning up…";
        Api.destroyDeployment(dep.id)
          .then(() => onChange())
          .catch((err) => {
            btn.disabled = false;
            btn.textContent = "Clean up";
            window.alert(err && err.message ? err.message : "Cleanup failed.");
          });
      });
      actions.appendChild(btn);
    }
    cells.push(actions);
    return el("tr", {}, cells);
  }

  function renderDeployments(mount, initialFilter) {
    const state = { filter: initialFilter || "open", query: "" };

    function load() {
      mount.innerHTML = "";
      mount.appendChild(el("p", { class: "muted", text: "Loading deployments…" }));
      Api.listDeployments("all")
        .then((deps) => {
          mount.innerHTML = "";
          deps = deps || [];
          const live = deps.filter((d) => LIVE.indexOf(d.status) !== -1);
          const failed = deps.filter((d) => d.status === "failed");

          mount.appendChild(
            el("div", { class: "stat-grid" }, [
              statCard("Active or in progress (loaded)", live.length),
              statCard("Failed - check resources (loaded)", failed.length),
              statCard("Records loaded", deps.length),
            ])
          );

          const filter = el("select", { class: "deploy-input", "aria-label": "Deployment status" }, [
            ["open", "Not destroyed"], ["failed", "Failed"], ["overdue", "Cleanup overdue"], ["expiringSoon", "Ends within 24 hours"],
            ["active", "Active"], ["deploying", "Deploying"], ["destroying", "Cleaning up"], ["destroyed", "Destroyed"], ["all", "All statuses"],
          ].map(([value, label]) => el("option", { value: value, text: label })));
          filter.value = state.filter;
          filter.addEventListener("change", () => {
            state.filter = filter.value;
            renderTable();
          });
          const search = searchControl("Search workload, owner, subscription or resource group");
          search.value = state.query;
          search.addEventListener("input", () => { state.query = search.value; renderTable(); });
          const count = el("p", { class: "muted small", "aria-live": "polite" });
          function filteredRows() {
            const query = state.query.trim().toLowerCase();
            return deps.filter((dep) => attentionMatch(dep, state.filter) &&
              [dep.useCase, dep.userUpn, dep.userName, dep.subscriptionId, dep.resourceGroup].join(" ").toLowerCase().includes(query));
          }
          mount.appendChild(el("div", { class: "deploy-toolbar" }, [search, filter,
            toolButton("\u21bb", "Refresh deployments", load),
            toolButton("\u2193", "Export filtered deployments as CSV", () => exportCsv("deployments.csv", [
              ["ID", "Workload", "Environment", "Engine", "Owner", "Status", "Created", "Ends", "Subscription", "Resource group", "Cleanup policy", "Detail"],
              ...filteredRows().map((dep) => [dep.id, dep.useCase, dep.environment, dep.engine, dep.userUpn, dep.status,
                dep.createdAt, dep.endAt, dep.subscriptionId, dep.resourceGroup, dep.cleanupPolicy, dep.detail]),
            ])),
          ]));
          mount.appendChild(count);

          const tableWrap = el("div", { class: "deploy-table-wrap" });
          mount.appendChild(tableWrap);

          function renderTable() {
            const rows = filteredRows();
            count.textContent = rows.length + " matching / " + deps.length + " loaded" + (deps.length >= 500 ? " (latest 500; older records not included)" : "");
            tableWrap.innerHTML = "";
            if (!rows.length) {
              tableWrap.appendChild(
                el("p", { class: "muted", text: "No deployments match these filters." })
              );
              return;
            }
            const head = el("tr", {}, [
              el("th", { text: "Use case" }),
              el("th", { text: "Env" }),
              el("th", { text: "Owner" }),
              el("th", { text: "Status" }),
              el("th", { text: "Created / elapsed" }),
              el("th", { text: "Cleanup due" }),
              el("th", { text: "Resource group / subscription" }),
              el("th", { text: "Actions" }),
            ]);
            tableWrap.appendChild(
              el("table", { class: "deploy-table" }, [
                el("thead", {}, head),
                el("tbody", {}, rows.map((d) => deployRow(d, load))),
              ])
            );
          }

          renderTable();
        })
        .catch((err) => {
          mount.innerHTML = "";
          mount.appendChild(
            el("p", { class: "error-text", text: err && err.message ? err.message : "Could not load deployments." })
          );
        });
    }

    load();
  }

  function subRow(sub, onChange) {
    const owner = el("td", {}, [
      el("strong", { text: sub.userName || sub.userUpn }),
      el("div", { class: "muted small", text: sub.userUpn }),
    ]);
    const files = el("td", {}, el("span", { class: "muted small", text: (sub.files || []).length + " files" }));

    const actions = el("td", { class: "row-actions" });
    // Primary path: a review branch exists -> one-click "Open pull request".
    if (sub.compareUrl && sub.status === "branched") {
      actions.appendChild(
        el("a", {
          href: sub.compareUrl, target: "_blank", rel: "noopener",
          class: "link-btn", text: "Review changes",
        })
      );
    }
    if (sub.status !== "discarded") {
      actions.appendChild(
        el("a", {
          href: Api.submissionDownloadUrl(sub.id), class: "link-btn",
          text: "Download", download: "",
        })
      );
      const btn = el("button", { type: "button", class: "danger-btn", text: "Delete submission" });
      btn.disabled = sub.status === "merged";
      btn.addEventListener("click", () => {
        const remote = sub.branch ? " This also deletes the GitHub review branch " + sub.branch + " in its original repository. Open pull requests may be closed. Published or merged code is not removed." : " No GitHub branch is recorded.";
        if (!window.confirm("Delete the submission \"" + (sub.displayName || sub.slug) + "\" and its staged files?" + remote)) return;
        btn.disabled = true;
        btn.textContent = "Deleting\u2026";
        Api.discardSubmission(sub.id)
          .then(() => onChange())
          .catch((err) => {
            btn.disabled = false;
            btn.textContent = "Delete submission";
            window.alert(err && err.message ? err.message : "Deletion failed; refresh to verify the submission state.");
          });
      });
      actions.appendChild(btn);
    }

    const useCaseCell = el("td", {}, [
      el("strong", { text: sub.displayName || sub.slug }),
      el("div", { class: "muted small", text: sub.slug }),
    ]);
    if (sub.branch) {
      useCaseCell.appendChild(el("div", { class: "muted small" }, el("code", { text: sub.branch })));
    }

    return el("tr", {}, [
      useCaseCell,
      owner,
      el("td", {}, [statusBadge(sub.status), el("div", { class: "muted small", text: sub.detail || "" })]),
      el("td", { text: fmtDate(sub.createdAt) }),
      files,
      actions,
    ]);
  }

  function renderSubmissions(mount, initialFilter) {
    const state = { filter: initialFilter || "open", query: "" };
    mount.classList.add("admin-submissions");
    mount.innerHTML = "";
    mount.appendChild(el("p", { class: "muted", text: "Loading submissions\u2026" }));

    function load() {
      Api.adminSubmissions()
        .then((subs) => {
          mount.innerHTML = "";
          subs = subs || [];
          const search = searchControl("Search submission, submitter or branch");
          search.value = state.query;
          const filter = el("select", { class: "deploy-input", "aria-label": "Submission status" }, [
            ["open", "Awaiting action"], ["pending", "Staged locally"], ["branched", "Review branch created"],
            ["discarded", "Deleted"], ["merged", "Merged"], ["all", "All statuses"],
          ].map(([value, label]) => el("option", { value: value, text: label })));
          filter.value = state.filter;
          const count = el("p", { class: "muted small", "aria-live": "polite" });
          const table = el("div", { class: "deploy-table-wrap" });
          function rows() {
            return subs.filter((sub) => (state.filter === "all" || (state.filter === "open" ? ["pending", "branched"].includes(sub.status) : sub.status === state.filter)) &&
              [sub.slug, sub.displayName, sub.userName, sub.userUpn, sub.branch].join(" ").toLowerCase().includes(state.query.trim().toLowerCase()));
          }
          search.addEventListener("input", () => { state.query = search.value; renderTable(); });
          filter.addEventListener("change", () => { state.filter = filter.value; renderTable(); });
          mount.appendChild(el("div", { class: "deploy-toolbar" }, [search, filter,
            toolButton("\u21bb", "Refresh submissions", load),
            toolButton("\u2193", "Export filtered submissions as CSV", () => exportCsv("submissions.csv", [
              ["ID", "Submission", "Submitter", "Status", "Submitted", "Files", "Branch", "Review URL", "Detail"],
              ...rows().map((sub) => [sub.id, sub.slug, sub.userUpn, sub.status, sub.createdAt, (sub.files || []).length, sub.branch, sub.compareUrl, sub.detail]),
            ])),
          ]));
          mount.appendChild(count);
          mount.appendChild(table);
          function renderTable() {
            const matching = rows();
            count.textContent = matching.length + " matching / " + subs.length + " loaded" + (subs.length >= 200 ? " (latest 200; older records not included)" : "");
            table.replaceChildren();
            if (!matching.length) {
              table.appendChild(el("p", { class: "muted", text: "No submissions match these filters." }));
              return;
            }
            const head = el("tr", {}, [
              el("th", { text: "Use case" }),
              el("th", { text: "Submitted by" }),
              el("th", { text: "Status / detail" }),
              el("th", { text: "When" }),
              el("th", { text: "Files" }),
              el("th", { text: "Actions" }),
            ]);
            table.appendChild(el("table", { class: "deploy-table" }, [
              el("thead", {}, head),
              el("tbody", {}, matching.map((sub) => subRow(sub, load))),
            ]));
          }
          renderTable();
        })
        .catch((err) => {
          mount.innerHTML = "";
          mount.appendChild(
            el("p", { class: "error-text", text: err && err.message ? err.message : "Could not load submissions." })
          );
        });
    }

    load();
  }

  function renderCatalog(mount) {
    mount.classList.add("admin-catalog");
    const search = searchControl("Search use cases");
    const filter = el("select", { class: "deploy-input", "aria-label": "Use-case visibility" }, [
      ["published", "Published"], ["removed", "Removed"], ["all", "All use cases"],
    ].map(([value, label]) => el("option", { value, text: label })));
    filter.value = "published";
    const status = el("p", { class: "muted small", role: "status" });
    const count = el("p", { class: "muted small", "aria-live": "polite" });
    const table = el("div", { class: "deploy-table-wrap" });
    const refresh = toolButton("\u21bb", "Refresh use cases", () => load());
    let catalog = { items: [] };
    let busy = false;
    mount.append(el("div", { class: "deploy-toolbar" }, [search, filter, refresh]), status, count, table);

    function renderTable() {
      refresh.disabled = busy;
      search.disabled = filter.disabled = busy;
      table.replaceChildren();
      const query = search.value.trim().toLowerCase();
      const rows = catalog.items.filter(item => (filter.value === "all" || item.removed === (filter.value === "removed")) &&
        [item.path, item.displayName, item.category].join(" ").toLowerCase().includes(query));
      count.textContent = rows.length + " matching / " + catalog.items.length + " loaded";
      if (!rows.length) {
        table.appendChild(el("p", { class: "muted", text: "No use cases match these filters." }));
        return;
      }
      const head = el("tr", {}, ["Use case", "Category", "Site status", "Removed", "Action"].map(text => el("th", { text })));
      table.appendChild(el("table", { class: "deploy-table" }, [el("thead", {}, head), el("tbody", {}, rows.map(item => {
        const portalLabel = item.removed ? "Restore to portal" : "Remove from portal";
        const repositoryOption = el("option", { value: "repository", text: "Remove from repository" });
        repositoryOption.disabled = !catalog.repositoryRemovalAvailable || !catalog.sourceCommit || !catalog.branch;
        const choice = el("select", { class: "deploy-input", "aria-label": "Removal action for " + item.displayName }, [
          el("option", { value: "portal", text: portalLabel }), repositoryOption,
        ]);
        choice.value = "portal";
        const action = toolButton(item.removed ? "\u21b6" : "\u2212", portalLabel + ": " + item.displayName, () => change(item, choice.value));
        action.disabled = busy;
        choice.disabled = busy;
        choice.addEventListener("change", () => {
          const label = (choice.value === "repository" ? "Remove from repository" : portalLabel) + ": " + item.displayName;
          action.title = label;
          action.setAttribute("aria-label", label);
          action.textContent = choice.value === "portal" && item.removed ? "\u21b6" : "\u2212";
          action.classList.toggle("danger-btn", choice.value === "repository" || !item.removed);
        });
        if (!item.removed) action.classList.add("danger-btn");
        return el("tr", {}, [
          el("td", {}, [el("strong", { text: item.displayName }), el("div", { class: "muted small", text: item.path })]),
          el("td", { text: item.category || "\u2014" }),
          el("td", { text: item.removed ? item.inSnapshot ? "Removed" : "Removed / absent from latest sync" : "Published" }),
          el("td", { text: item.removedAt ? fmtDate(item.removedAt * 1000) : "\u2014" }),
          el("td", {}, el("div", { class: "admin-catalog-actions" }, [choice, action])),
        ]);
      }))]));
    }

    async function load(message = "") {
      if (busy) return;
      busy = true;
      status.className = "muted small";
      status.textContent = "Loading use cases...";
      renderTable();
      try {
        const result = await Api.adminCatalog();
        if (!mount.isConnected) return;
        catalog = { ...result, items: result.items || [] };
        status.textContent = result.supported === false ? "Catalog management is unavailable in this distribution." : message;
      } catch (error) {
        status.className = "error-text";
        status.textContent = error.message || "Could not load use cases.";
      } finally { busy = false; renderTable(); }
    }

    async function change(item, destination) {
      if (busy) return;
      const repositoryRemoval = destination === "repository";
      if (repositoryRemoval && !catalog.repositoryRemovalAvailable) return;
      const message = repositoryRemoval
        ? `Delete catalog/${item.path}/ and ALL files inside it directly from ${catalog.repository} / ${catalog.branch} now? This commits the deletion to that branch, not a review branch, and removes "${item.displayName}" from the portal. Shared modules, other use cases, existing deployments and Azure resources are NOT deleted. Restore to portal cannot undo this; recovery requires restoring source from Git history. GitHub branch protection will not be bypassed.`
        : item.removed
          ? `Restore "${item.displayName}" to the portal? It will be available for new deployments if present in the latest synced catalog. Repository files and existing deployments are unchanged.`
          : `Remove "${item.displayName}" from the portal? It will be hidden and blocked for new deployments, including after sync. Repository files, existing deployments and Azure resources will NOT be deleted. You can restore it from the Removed filter.`;
      if (!confirm(message)) return;
      busy = true;
      status.className = "muted small";
      status.textContent = repositoryRemoval ? "Deleting use-case folder from repository..." : item.removed ? "Restoring use case..." : "Removing use case...";
      renderTable();
      try {
        if (repositoryRemoval) {
          const result = await Api.requestCatalogRepositoryRemoval(item.path, catalog.repository, catalog.version, catalog.branch, catalog.sourceCommit);
          const url = new URL(result.url);
          if (result.status !== "deleted" || result.path !== item.path || result.branch !== catalog.branch || !/^[a-f0-9]{40}$/.test(result.commit) ||
              url.origin !== "https://github.com" || url.username || url.password || url.hash || url.search ||
              url.pathname !== `/${catalog.repository}/commit/${result.commit}`) throw new Error("Repository deletion could not be verified. It may have committed; inspect the configured branch and refresh before retrying.");
          catalog.items = catalog.items.filter(row => row.path !== item.path);
          document.dispatchEvent(new CustomEvent("techlabs:catalog-changed"));
          status.replaceChildren(document.createTextNode(`catalog/${item.path}/ is absent from ${catalog.repository} / ${catalog.branch}. Removed from the portal. Existing deployments are unchanged. `),
            el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", text: "View verified commit" }));
          busy = false;
          renderTable();
          return;
        }
        await Api.setCatalogRemoved(item.path, catalog.repository, catalog.version, !item.removed);
        document.dispatchEvent(new CustomEvent("techlabs:catalog-changed"));
      } catch (error) {
        status.className = "error-text";
        status.textContent = error.message || "The use-case change could not be confirmed. Refresh the list before retrying.";
        busy = false;
        renderTable();
        return;
      }
      busy = false;
      await load(item.removed ? "Removal cleared. The latest synced source controls availability." : "Removed from the portal. Repository files and existing deployments are unchanged.");
    }
    search.addEventListener("input", renderTable);
    filter.addEventListener("change", renderTable);
    load();
  }

  function renderTelemetry(mount, navigate) {
    const period = el("select", { class: "deploy-input", "aria-label": "Reporting period" },
      [7, 30, 90].map((days) => el("option", { value: days, text: "Last " + days + " days" })));
    period.value = "30";
    const updated = el("span", { class: "muted small", "aria-live": "polite" });
    const content = el("div");
    let current = null;
    let version = 0;
    const download = toolButton("\u2193", "Export operations report as CSV", () => {
      if (!current) return;
      const rows = [["Scope", "Metric", "Count"],
        ["Generated at", new Date(current.generatedAt * 1000).toISOString(), ""],
        ["Activity since", new Date(current.since * 1000).toISOString(), ""],
        ["Last " + current.days + " days", "Deployment requests", current.periodDeployments],
        ["Last " + current.days + " days", "Unique users", current.uniqueUsers]];
      Object.entries(current.operations || {}).forEach(([key, value]) => rows.push(["Current", key, value]));
      Object.entries(current.statusCounts || {}).forEach(([key, value]) => rows.push(["All-time deployment status", key, value]));
      Object.entries(current.submissionCounts || {}).forEach(([key, value]) => rows.push(["Current submissions", key, value]));
      (current.topDeploys || []).forEach((item) => rows.push(["Top workloads in period", item.use_case, item.n]));
      (current.perDay || []).forEach((item) => rows.push(["Daily requests UTC", item.d, item.n]));
      exportCsv("operations-" + current.days + "d.csv", rows);
    });
    const refresh = toolButton("\u21bb", "Refresh operations", load);
    mount.replaceChildren(el("div", { class: "deploy-toolbar" }, [period, updated, refresh, download]), content);
    period.addEventListener("change", load);
    function load() {
      const request = ++version;
      current = null;
      download.disabled = true;
      updated.textContent = "Loading report...";
      content.replaceChildren();
      Api.adminTelemetry(Number(period.value)).then((report) => {
        if (request !== version || !mount.contains(content)) return;
        current = report;
        download.disabled = false;
        updated.textContent = "Updated " + fmtDate(report.generatedAt * 1000);
        const operations = report.operations || {};
        const counts = report.submissionCounts || {};
        const queue = el("div", { class: "admin-attention" });
        [
          [operations.failed || 0, "Failed deployments", "dep", "failed", "danger"],
          [operations.overdue || 0, "Cleanup overdue", "dep", "overdue", "danger"],
          [operations.expiringSoon || 0, "Ends within 24 hours", "dep", "expiringSoon", "warning"],
          [counts.pending || 0, "Staged locally", "sub", "pending", "warning"],
          [counts.branched || 0, "Review branches", "sub", "branched", ""],
        ].forEach(([count, label, tab, filter, tone]) => {
          const action = el("button", { type: "button", class: "admin-attention-item " + (count ? tone : ""), "aria-label": label + ": " + count },
            [el("strong", { text: count }), el("span", { text: label }), el("span", { text: "\u2192", "aria-hidden": "true" })]);
          action.addEventListener("click", () => navigate(tab, filter));
          queue.appendChild(action);
        });
        content.appendChild(el("section", { class: "admin-section" }, [el("h3", { text: "Current operations" }), queue,
          el("p", { class: "muted small", text: (operations.inProgress || 0) + " operations in progress · " + (operations.externallyManaged || 0) + " deployments with external cleanup · " + sumValues(report.statusCounts) + " total tracked" }),
        ]));
        const catalog = report.catalog || {};
        const job = catalog.job || {};
        const processing = catalog.processing || {};
        const catalogRows = [
          ["Repository", catalog.repository ? catalog.repository + " / " + catalog.branch : "Not connected"],
          ["Last successful sync", catalog.syncedAt ? fmtDate(catalog.syncedAt * 1000) : "Never"],
          ["Published workloads", String(catalog.itemCount || 0)],
          ["Removed from site", String(catalog.removedCount || 0)],
          ["Processing", processing.mode || "container"],
          ["Job", job.status || "Not started"],
          ["Regional price / capacity refresh", catalog.regionalJob?.status || "Waiting for schedule"],
          ["Next regional refresh", catalog.regionalJob?.nextRunAt ? fmtDate(catalog.regionalJob.nextRunAt * 1000) : "Automatic / runner schedule"],
          ["Roadmap refresh", catalog.lifecycleJob?.status || "Waiting for schedule"],
        ];
        content.appendChild(el("section", { class: "admin-section" }, [el("h3", { text: "Catalog health" }),
          el("dl", { class: "admin-facts" }, catalogRows.flatMap(([label, value]) => [el("dt", { text: label }), el("dd", { text: value })])),
          job.detail ? el("p", { class: job.status === "failed" ? "error-text" : "muted small", text: job.detail }) : null,
          catalog.lifecycleJob?.detail ? el("p", { class: catalog.lifecycleJob.status === "failed" ? "error-text" : "muted small", text: catalog.lifecycleJob.detail }) : null,
          processing.fallback ? el("p", { class: "muted small", text: "Container fallback: " + processing.fallback }) : null,
        ]));
        const days = Number(report.days || period.value);
        const perDay = [];
        const dailyCounts = new Map((report.perDay || []).map((item) => [item.d, item.n]));
        const beginning = new Date((report.since || Date.now() / 1000 - days * 86400) * 1000);
        beginning.setUTCHours(0, 0, 0, 0);
        const end = (report.generatedAt || Date.now() / 1000) * 1000;
        for (let stamp = beginning.getTime(); stamp <= end; stamp += 86400000) {
          const date = new Date(stamp).toISOString().slice(0, 10);
          perDay.push({ d: date, n: dailyCounts.get(date) || 0 });
        }
        content.appendChild(el("section", { class: "admin-section" }, [
          el("h3", { text: "Activity / last " + days + " days" }),
          el("p", { class: "muted small", text: "Since " + fmtDate((report.since || beginning.getTime() / 1000) * 1000) + "; daily buckets use UTC, first and last days are partial." }),
          el("div", { class: "stat-grid" }, [statCard("Deployment requests", report.periodDeployments || 0),
            statCard("Unique users", report.uniqueUsers || 0), statCard("Recorded events", sumValues(report.byType))]),
          el("div", { class: "chart-grid" }, [barChart("Requests by day (UTC)", perDay, "d", "n"),
            barChart("Most requested workloads", report.topDeploys || [], "use_case", "n"),
            barChart("Current status of requests in this period", dictToItems(report.periodStatusCounts), "k", "n"),
            barChart("Deployment requests by owner", report.topUsers || [], "user_upn", "n")]),
        ]));
      }).catch((error) => {
        if (request !== version || !mount.contains(content)) return;
        updated.textContent = "Report unavailable";
        content.replaceChildren(el("p", { class: "error-text", role: "alert", text: error.message || "Could not load operations." }));
      });
    }
    load();
  }

  function renderUsers(mount) {
    mount.innerHTML = "";
    mount.appendChild(el("p", { class: "muted", text: "Loading users…" }));

    function load() {
      Api.adminUsers()
        .then((users) => {
          mount.innerHTML = "";

          const upnInput = el("input", { type: "email", class: "deploy-input", placeholder: "user@example.com" });
          const roleSel = el("select", { class: "deploy-input" }, [
            el("option", { value: "guest", text: "guest" }),
            el("option", { value: "admin", text: "admin" }),
          ]);
          const addBtn = el("button", { type: "button", class: "deploy-btn", text: "Add / update" });
          const addMsg = el("span", { class: "form-msg" });
          addBtn.addEventListener("click", () => {
            const upn = upnInput.value.trim();
            if (!upn || upn.indexOf("@") === -1) {
              addMsg.textContent = "Enter a valid UPN.";
              addMsg.className = "form-msg err";
              return;
            }
            addBtn.disabled = true;
            addMsg.textContent = "Saving…";
            addMsg.className = "form-msg";
            Api.setUserRole(upn, roleSel.value)
              .then(() => {
                upnInput.value = "";
                addBtn.disabled = false;
                load();
              })
              .catch((err) => {
                addBtn.disabled = false;
                addMsg.textContent = err && err.message ? err.message : "Failed.";
                addMsg.className = "form-msg err";
              });
          });

          mount.appendChild(
            el("div", { class: "add-user" }, [
              el("h3", { text: "Add or change a user" }),
              el("div", { class: "add-user-row" }, [upnInput, roleSel, addBtn]),
              addMsg,
              el("p", { class: "deploy-note", text: "Admins manage users and see all data. Everyone else is a guest who can deploy and see only their own deployments." }),
            ])
          );

          const head = el("tr", {}, [
            el("th", { text: "User" }),
            el("th", { text: "Role" }),
            el("th", { text: "Last seen" }),
            el("th", { text: "" }),
          ]);
          const tbody = el("tbody", {}, users.map((u) => userRow(u, load)));
          mount.appendChild(
            el("table", { class: "deploy-table" }, [el("thead", {}, head), tbody])
          );
        })
        .catch((err) => {
          mount.innerHTML = "";
          mount.appendChild(
            el("p", { class: "error-text", text: err && err.message ? err.message : "Could not load users." })
          );
        });
    }

    load();
  }

  function userRow(u, onChange) {
    const roleSel = el("select", { class: "deploy-input role-select" }, [
      el("option", { value: "guest", text: "guest" }),
      el("option", { value: "admin", text: "admin" }),
    ]);
    roleSel.value = u.role;
    const msg = el("span", { class: "form-msg" });
    roleSel.addEventListener("change", () => {
      const prev = u.role;
      roleSel.disabled = true;
      Api.setUserRoleById(u.oid, roleSel.value)
        .then(() => {
          u.role = roleSel.value;
          roleSel.disabled = false;
          onChange();
        })
        .catch((err) => {
          roleSel.value = prev;
          roleSel.disabled = false;
          msg.textContent = err && err.message ? err.message : "Failed.";
          msg.className = "form-msg err";
        });
    });

    const who = el("td", {}, [
      el("strong", { text: u.name || u.upn }),
      el("div", { class: "muted small", text: u.upn }),
    ]);
    const role = el("td", {}, [roleSel, u.pending ? el("span", { class: "chip", text: "invited" }) : null, msg]);
    return el("tr", {}, [who, role, el("td", { text: u.lastSeen ? new Date(u.lastSeen).toLocaleString() : "—" }), el("td")]);
  }

  function render(body) {
    body.innerHTML = "";
    const wrapper = el("div", { class: "admin-view" });
    body.appendChild(wrapper);
    body = wrapper;
    body.appendChild(el("h2", { text: "Administration" }));

    const tabs = el("div", { class: "tabs" });
    const telBtn = el("button", { type: "button", class: "tab is-active", text: "Operations" });
    const depBtn = el("button", { type: "button", class: "tab", text: "Deployments" });
    const subBtn = el("button", { type: "button", class: "tab", text: "Submissions" });
    const catBtn = el("button", { type: "button", class: "tab", text: "Use cases" });
    const usrBtn = el("button", { type: "button", class: "tab", text: "Users" });
    tabs.appendChild(telBtn);
    tabs.appendChild(depBtn);
    tabs.appendChild(subBtn);
    tabs.appendChild(catBtn);
    tabs.appendChild(usrBtn);
    body.appendChild(tabs);

    let mount = el("div", { class: "tab-mount" });
    body.appendChild(mount);

    function show(which, filter) {
      const next = el("div", { class: "tab-mount" });
      mount.replaceWith(next);
      mount = next;
      telBtn.classList.toggle("is-active", which === "tel");
      depBtn.classList.toggle("is-active", which === "dep");
      subBtn.classList.toggle("is-active", which === "sub");
      catBtn.classList.toggle("is-active", which === "cat");
      usrBtn.classList.toggle("is-active", which === "usr");
      if (which === "tel") renderTelemetry(mount, show);
      else if (which === "dep") renderDeployments(mount, filter);
      else if (which === "sub") renderSubmissions(mount, filter);
      else if (which === "cat") renderCatalog(mount);
      else renderUsers(mount);
    }
    telBtn.addEventListener("click", () => show("tel"));
    depBtn.addEventListener("click", () => show("dep"));
    subBtn.addEventListener("click", () => show("sub"));
    catBtn.addEventListener("click", () => show("cat"));
    usrBtn.addEventListener("click", () => show("usr"));
    show("tel");
  }

  trigger.addEventListener("click", () => Panel.open(render));
})();
