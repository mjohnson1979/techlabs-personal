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
    const usrBtn = el("button", { type: "button", class: "tab", text: "Users" });
    tabs.appendChild(telBtn);
    tabs.appendChild(usrBtn);
    body.appendChild(tabs);

    const mount = el("div", { class: "tab-mount" });
    body.appendChild(mount);

    function show(which) {
      telBtn.classList.toggle("is-active", which === "tel");
      usrBtn.classList.toggle("is-active", which === "usr");
      if (which === "tel") renderTelemetry(mount);
      else renderUsers(mount);
    }
    telBtn.addEventListener("click", () => show("tel"));
    usrBtn.addEventListener("click", () => show("usr"));
    show("tel");
  }

  trigger.addEventListener("click", () => Panel.open(render));
})();
