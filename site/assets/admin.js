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

  function statCard(label, value) {
    return el("div", { class: "stat-card" }, [
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
      const bar = el("span", { class: "bar-fill" });
      bar.style.width = Math.round((it[valueKey] / max) * 100) + "%";
      rows.appendChild(
        el("div", { class: "bar-row" }, [
          el("span", { class: "bar-label", text: String(it[labelKey]) }),
          el("span", { class: "bar-track" }, bar),
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
    return el("span", { class: "badge badge-" + status, text: status });
  }

  function deployRow(dep, onChange) {
    const owner = el("td", {}, [
      el("strong", { text: dep.userName || dep.userUpn }),
      el("div", { class: "muted small", text: dep.userUpn }),
    ]);
    const ended = dep.status === "destroyed" || dep.status === "failed";
    const running = fmtDuration(dep.createdAt, ended ? dep.destroyedAt || dep.endAt : null);

    const cells = [
      el("td", {}, el("strong", { text: dep.useCase })),
      el("td", {}, el("span", { class: "chip", text: dep.environment })),
      owner,
      el("td", {}, [
        statusBadge(dep.status),
        dep.status === "failed" && dep.detail
          ? el("div", { class: "muted small", text: dep.detail, title: dep.detail })
          : null,
      ]),
      el("td", { text: fmtDate(dep.createdAt) }),
      el("td", { text: running }),
      el("td", { text: fmtDate(dep.endAt) }),
      el("td", {}, el("code", { text: dep.resourceGroup })),
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
    if (CLEANABLE.indexOf(dep.status) !== -1) {
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

  function renderDeployments(mount) {
    const state = { activeOnly: true };

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
              statCard("Active deployments", live.length),
              statCard("Failed - check resources", failed.length),
              statCard("Total tracked", deps.length),
            ])
          );

          const checkbox = el("input", { type: "checkbox" });
          checkbox.checked = state.activeOnly;
          checkbox.addEventListener("change", () => {
            state.activeOnly = checkbox.checked;
            renderTable();
          });
          const toggle = el("label", { class: "toggle" }, [checkbox, el("span", { text: "Hide destroyed" })]);
          const refreshBtn = el("button", { type: "button", class: "deploy-btn", text: "Refresh" });
          refreshBtn.addEventListener("click", load);
          mount.appendChild(el("div", { class: "deploy-toolbar" }, [toggle, refreshBtn]));

          const tableWrap = el("div", { class: "deploy-table-wrap" });
          mount.appendChild(tableWrap);

          function renderTable() {
            const rows = state.activeOnly ? deps.filter((d) => d.status !== "destroyed") : deps;
            tableWrap.innerHTML = "";
            if (!rows.length) {
              tableWrap.appendChild(
                el("p", { class: "muted", text: state.activeOnly ? "No deployments holding resources." : "No deployments tracked yet." })
              );
              return;
            }
            const head = el("tr", {}, [
              el("th", { text: "Use case" }),
              el("th", { text: "Env" }),
              el("th", { text: "Owner" }),
              el("th", { text: "Status" }),
              el("th", { text: "Created" }),
              el("th", { text: "Running for" }),
              el("th", { text: "Ends" }),
              el("th", { text: "Resource group" }),
              el("th", { text: "" }),
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
    if (sub.compareUrl) {
      actions.appendChild(
        el("a", {
          href: sub.compareUrl, target: "_blank", rel: "noopener",
          class: "link-btn", text: "Open pull request",
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
      const btn = el("button", { type: "button", class: "danger-btn", text: "Discard" });
      btn.addEventListener("click", () => {
        if (!window.confirm("Discard the submission \"" + (sub.displayName || sub.slug) + "\"? This deletes the staged files.")) return;
        btn.disabled = true;
        btn.textContent = "Discarding\u2026";
        Api.discardSubmission(sub.id)
          .then(() => onChange())
          .catch((err) => {
            btn.disabled = false;
            btn.textContent = "Discard";
            window.alert(err && err.message ? err.message : "Discard failed.");
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
      el("td", {}, statusBadge(sub.status)),
      el("td", { text: fmtDate(sub.createdAt) }),
      files,
      actions,
    ]);
  }

  function renderSubmissions(mount) {
    mount.innerHTML = "";
    mount.appendChild(el("p", { class: "muted", text: "Loading submissions\u2026" }));

    function load() {
      Api.adminSubmissions()
        .then((subs) => {
          mount.innerHTML = "";
          subs = subs || [];
          const pending = subs.filter((s) => s.status === "pending");

          mount.appendChild(
            el("div", { class: "stat-grid" }, [
              statCard("Pending review", pending.length),
              statCard("Total submitted", subs.length),
            ])
          );
          mount.appendChild(
            el("p", {
              class: "muted small",
              text: "Uploaded templates are validated and committed to a review branch. Click \u201COpen pull request\u201D to review and merge; Download grabs the bundle; Discard removes the staged files.",
            })
          );

          if (!subs.length) {
            mount.appendChild(el("p", { class: "muted", text: "No submissions yet." }));
            return;
          }
          const head = el("tr", {}, [
            el("th", { text: "Use case" }),
            el("th", { text: "Submitted by" }),
            el("th", { text: "Status" }),
            el("th", { text: "When" }),
            el("th", { text: "Files" }),
            el("th", { text: "" }),
          ]);
          mount.appendChild(
            el("div", { class: "deploy-table-wrap" }, [
              el("table", { class: "deploy-table" }, [
                el("thead", {}, head),
                el("tbody", {}, subs.map((s) => subRow(s, load))),
              ]),
            ])
          );
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

  function renderTelemetry(mount) {
    mount.innerHTML = "";
    mount.appendChild(el("p", { class: "muted", text: "Loading telemetry…" }));
    Api.adminTelemetry(30)
      .then((t) => {
        mount.innerHTML = "";
        const totalDeploys = sumValues(t.statusCounts);
        const totalEvents = sumValues(t.byType);

        mount.appendChild(
          el("div", { class: "stat-grid" }, [
            statCard("Active deployments", t.activeDeployments),
            statCard("Unique users (30d)", t.uniqueUsers),
            statCard("Total deployments", totalDeploys),
            statCard("Tracked events (30d)", totalEvents),
          ])
        );

        mount.appendChild(
          el("div", { class: "chart-grid" }, [
            barChart("Top deployed use cases", t.topDeploys, "use_case", "n"),
            barChart("Most viewed use cases", t.topViews, "use_case", "n"),
            barChart("Top deployers", t.topUsers, "user_upn", "n"),
            barChart("Deployment status", dictToItems(t.statusCounts), "k", "n"),
            barChart("Deployments per day", t.perDay, "d", "n"),
            barChart("Events by type", dictToItems(t.byType), "k", "n"),
          ])
        );
      })
      .catch((err) => {
        mount.innerHTML = "";
        mount.appendChild(
          el("p", { class: "error-text", text: err && err.message ? err.message : "Could not load telemetry." })
        );
      });
  }

  function renderUsers(mount) {
    mount.innerHTML = "";
    mount.appendChild(el("p", { class: "muted", text: "Loading users…" }));

    function load() {
      Api.adminUsers()
        .then((users) => {
          mount.innerHTML = "";

          const upnInput = el("input", { type: "email", class: "deploy-input", placeholder: "user@microsoft.com" });
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
      Api.setUserRole(u.upn, roleSel.value)
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
    body.appendChild(el("h2", { text: "Admin" }));

    const tabs = el("div", { class: "tabs" });
    const telBtn = el("button", { type: "button", class: "tab is-active", text: "Telemetry" });
    const depBtn = el("button", { type: "button", class: "tab", text: "Deployments" });
    const subBtn = el("button", { type: "button", class: "tab", text: "Submissions" });
    const usrBtn = el("button", { type: "button", class: "tab", text: "Users" });
    tabs.appendChild(telBtn);
    tabs.appendChild(depBtn);
    tabs.appendChild(subBtn);
    tabs.appendChild(usrBtn);
    body.appendChild(tabs);

    const mount = el("div", { class: "tab-mount" });
    body.appendChild(mount);

    function show(which) {
      telBtn.classList.toggle("is-active", which === "tel");
      depBtn.classList.toggle("is-active", which === "dep");
      subBtn.classList.toggle("is-active", which === "sub");
      usrBtn.classList.toggle("is-active", which === "usr");
      if (which === "tel") renderTelemetry(mount);
      else if (which === "dep") renderDeployments(mount);
      else if (which === "sub") renderSubmissions(mount);
      else renderUsers(mount);
    }
    telBtn.addEventListener("click", () => show("tel"));
    depBtn.addEventListener("click", () => show("dep"));
    subBtn.addEventListener("click", () => show("sub"));
    usrBtn.addEventListener("click", () => show("usr"));
    show("tel");
  }

  trigger.addEventListener("click", () => Panel.open(render));
})();
