/* "My deployments" view: lists the signed-in user's deployments with status and
   a tear-down action. Opened from the user menu. */
(function () {
  "use strict";

  const el = (window.TechlabsDom && window.TechlabsDom.el) || null;
  const Panel = window.TechlabsPanel;
  const Api = window.TechlabsApi;
  const trigger = document.getElementById("menu-deployments");
  if (!el || !Panel || !Api || !trigger) return;

  const LIVE = ["requested", "deploying", "active", "expiring"];
  // A failed deploy can leave partially-created resources, so allow tearing it
  // down too.
  const CLEANABLE = ["requested", "deploying", "active", "expiring", "failed"];

  function fmt(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString();
  }

  function statusBadge(status) {
    return el("span", { class: "badge badge-" + status, text: status });
  }

  function row(dep, onChange) {
    const cells = [
      el("td", {}, el("strong", { text: dep.useCase })),
      el("td", {}, el("span", { class: "chip", text: dep.environment })),
      el("td", {}, statusBadge(dep.status)),
      el("td", { text: dep.externallyManaged ? "Externally managed" : fmt(dep.endAt) }),
      el("td", {}, el("code", { text: dep.resourceGroup })),
    ];

    const actions = el("td", { class: "row-actions" });
    if (dep.ghRunUrl) {
      actions.appendChild(
        el("a", { href: dep.ghRunUrl, target: "_blank", rel: "noopener", class: "link-btn", text: "Azure portal" })
      );
    }
    if (!dep.externallyManaged && CLEANABLE.indexOf(dep.status) !== -1) {
      const btn = el("button", { type: "button", class: "danger-btn", text: "Tear down" });
      btn.addEventListener("click", () => {
        if (!window.confirm("Tear down " + dep.useCase + " now? This deletes " + dep.resourceGroup + ".")) return;
        btn.disabled = true;
        btn.textContent = "Tearing down…";
        Api.destroyDeployment(dep.id)
          .then(() => onChange())
          .catch((err) => {
            btn.disabled = false;
            btn.textContent = "Tear down";
            window.alert(err && err.message ? err.message : "Teardown failed.");
          });
      });
      actions.appendChild(btn);
    }
    cells.push(actions);
    return el("tr", {}, cells);
  }

  function render(body) {
    body.innerHTML = "";
    body.appendChild(el("h2", { text: "My deployments" }));
    const mount = el("div", { class: "deploy-table-wrap" });
    body.appendChild(mount);

    function load() {
      mount.innerHTML = "";
      mount.appendChild(el("p", { class: "muted", text: "Loading…" }));
      Api.listDeployments("mine")
        .then((deps) => {
          mount.innerHTML = "";
          if (!deps || !deps.length) {
            mount.appendChild(el("p", { class: "muted", text: "No deployments yet. Pick a use case and deploy it." }));
            return;
          }
          const head = el("tr", {}, [
            el("th", { text: "Use case" }),
            el("th", { text: "Env" }),
            el("th", { text: "Status" }),
            el("th", { text: "Ends" }),
            el("th", { text: "Resource group" }),
            el("th", { text: "" }),
          ]);
          const table = el("table", { class: "deploy-table" }, [
            el("thead", {}, head),
            el("tbody", {}, deps.map((d) => row(d, load))),
          ]);
          mount.appendChild(table);
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

  trigger.addEventListener("click", () => Panel.open(render));
})();
