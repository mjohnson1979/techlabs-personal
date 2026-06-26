/* SME&C Techlabs gallery - loads catalog.json and renders filterable cards. */
(function () {
  "use strict";

  const REPO = "https://github.com/mcaps-microsoft/TechLabs";
  const state = { items: [], q: "", category: new Set(), industry: new Set(), compliance: new Set(), me: null };

  function pad2(n) {
    return String(n).padStart(2, "0");
  }
  // Format a Date as a value for <input type="datetime-local"> (local time).
  function localDatetimeValue(d) {
    return (
      d.getFullYear() +
      "-" +
      pad2(d.getMonth() + 1) +
      "-" +
      pad2(d.getDate()) +
      "T" +
      pad2(d.getHours()) +
      ":" +
      pad2(d.getMinutes())
    );
  }

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, props = {}, children = []) => {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    });
    (Array.isArray(children) ? children : [children]).forEach((c) => {
      if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  };

  // Format a number as currency (no decimals for figures >= 100).
  function fmtMoney(n, currency) {
    if (typeof n !== "number" || !isFinite(n)) return null;
    const cur = currency || "USD";
    const digits = n >= 100 ? 0 : 2;
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: cur,
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(n);
    } catch (e) {
      return `${cur} ${n.toFixed(digits)}`;
    }
  }

  function uniqueValues(key) {
    const set = new Set();
    state.items.forEach((it) => (it[key] || []).forEach((v) => set.add(v)));
    return Array.from(set).sort();
  }
  function uniqueScalar(key) {
    const set = new Set();
    state.items.forEach((it) => it[key] && set.add(it[key]));
    return Array.from(set).sort();
  }

  function buildFilter(containerId, values, bucket) {
    const container = $("#" + containerId);
    container.innerHTML = "";
    values.forEach((v) => {
      const cb = el("input", { type: "checkbox", value: v });
      cb.addEventListener("change", () => {
        cb.checked ? bucket.add(v) : bucket.delete(v);
        render();
      });
      container.appendChild(el("label", {}, [cb, el("span", { text: v })]));
    });
  }

  function matches(it) {
    if (state.q) {
      const hay = [it.displayName, it.description, it.what, it.why, (it.potentialUseCases || []).join(" ")]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(state.q)) return false;
    }
    if (state.category.size && !state.category.has(it.category)) return false;
    if (state.industry.size && !(it.industries || []).some((i) => state.industry.has(i))) return false;
    if (state.compliance.size && !(it.compliance || []).some((c) => state.compliance.has(c))) return false;
    return true;
  }

  function card(it) {
    const chips = [el("span", { class: "chip", text: it.path })];
    if (it.hasProd) chips.push(el("span", { class: "chip prod", text: "dev + prod" }));
    (it.industries || []).slice(0, 2).forEach((i) => chips.push(el("span", { class: "chip", text: i })));

    const node = el("article", { class: "card", tabindex: "0" }, [
      el("span", { class: "card-cat", text: it.category || "Other" }),
      el("h2", { text: it.displayName }),
      el("p", { class: "card-desc", text: it.description || "" }),
      cardCost(it),
      el("div", { class: "chips" }, chips),
    ]);
    node.addEventListener("click", () => openModal(it));
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter") openModal(it);
    });
    return node;
  }

  // Compact monthly-cost line shown on each card.
  function cardCost(it) {
    const cost = it.cost;
    const total = cost && typeof cost.totalMonthly === "number" ? cost.totalMonthly : null;
    const money = total !== null ? fmtMoney(total, cost.currency) : null;
    if (money && total > 0) {
      return el("div", { class: "card-cost" }, [
        el("span", { class: "card-cost-amount", text: `~${money}` }),
        el("span", { class: "card-cost-unit", text: "/mo est." }),
      ]);
    }
    return el("div", { class: "card-cost card-cost-muted" }, [
      el("span", { class: "card-cost-unit", text: "Usage-based — see details" }),
    ]);
  }

  function render() {
    const cards = $("#cards");
    cards.innerHTML = "";
    const filtered = state.items.filter(matches);
    filtered.forEach((it) => cards.appendChild(card(it)));
    $("#result-count").textContent = `${filtered.length} of ${state.items.length} use cases`;
    if (!filtered.length) {
      cards.appendChild(el("p", { class: "card-desc", text: "No use cases match your filters." }));
    }
  }

  function pillarBlock(wa) {
    if (!wa) return null;
    const labels = {
      reliability: "Reliability",
      security: "Security",
      costOptimization: "Cost Optimization",
      operationalExcellence: "Operational Excellence",
      performanceEfficiency: "Performance Efficiency",
    };
    const wrap = el("div", { class: "pillars" });
    Object.keys(labels).forEach((k) => {
      const arr = wa[k];
      if (!arr || !arr.length) return;
      wrap.appendChild(
        el("div", { class: "pillar" }, [
          el("h4", { text: labels[k] }),
          el("ul", {}, arr.map((x) => el("li", { text: x }))),
        ])
      );
    });
    return wrap;
  }

  function costBlock(it) {
    const cost = it.cost;
    if (!cost) return null;

    const wrap = el("div", { class: "modal-section" }, [el("h3", { text: "Cost estimate" })]);

    if (cost.profile) {
      wrap.appendChild(el("p", { text: cost.profile }));
    }

    const components = cost.components || [];
    const priced = components.some((c) => typeof c.monthly === "number");

    if (components.length && priced) {
      const head = el("div", { class: "cost-row cost-head" }, [
        el("span", { class: "cost-name", text: "Component" }),
        el("span", { class: "cost-qty", text: "Qty/mo" }),
        el("span", { class: "cost-amt", text: "Monthly" }),
      ]);
      const rows = components.map((c) => {
        const qty =
          c.quantityPerMonth !== undefined && c.quantityPerMonth !== null
            ? `${c.quantityPerMonth} ${c.unitOfMeasure || ""}`.trim()
            : "variable";
        const money = typeof c.monthly === "number" ? fmtMoney(c.monthly, cost.currency) : "—";
        const name = el("span", { class: "cost-name" }, [
          el("span", { text: c.name }),
          c.billing ? el("span", { class: "cost-tag", text: c.billing }) : null,
        ]);
        return el("div", { class: "cost-row", title: c.notes || "" }, [
          name,
          el("span", { class: "cost-qty", text: qty }),
          el("span", { class: "cost-amt", text: money }),
        ]);
      });
      const table = el("div", { class: "cost-table" }, [head, ...rows]);

      const totalRow = (label, value, cls) =>
        el("div", { class: `cost-row cost-total ${cls || ""}` }, [
          el("span", { class: "cost-name", text: label }),
          el("span", { class: "cost-qty", text: "" }),
          el("span", { class: "cost-amt", text: value }),
        ]);
      if (typeof cost.fixedMonthly === "number")
        table.appendChild(totalRow("Committed (fixed)", fmtMoney(cost.fixedMonthly, cost.currency)));
      if (typeof cost.usageMonthly === "number")
        table.appendChild(totalRow("Estimated usage", fmtMoney(cost.usageMonthly, cost.currency)));
      if (typeof cost.totalMonthly === "number")
        table.appendChild(
          totalRow("Total / month", fmtMoney(cost.totalMonthly, cost.currency), "cost-grand")
        );

      wrap.appendChild(table);

      const meta = [];
      if (cost.region) meta.push(`Region ${cost.region}`);
      if (cost.pricedAt) meta.push(`retail prices as of ${cost.pricedAt}`);
      meta.push("PAYG list price, excludes tax/reservations/free tiers");
      if (cost.complete === false) meta.push("some components not priced — see notes");
      wrap.appendChild(el("p", { class: "cost-note", text: meta.join(" · ") }));
    } else if (components.length) {
      // No prices available — fall back to the component list.
      wrap.appendChild(
        el(
          "ul",
          {},
          components.map((c) => {
            const qty =
              c.quantityPerMonth !== undefined ? `${c.quantityPerMonth} ${c.unitOfMeasure || "units"}` : "variable usage";
            const note = c.notes ? ` — ${c.notes}` : "";
            return el("li", { text: `${c.name} (${qty})${note}` });
          })
        )
      );
    }

    wrap.appendChild(
      el("div", { class: "code", text: `./tools/Get-CatalogCost.ps1 -UseCase ${it.path}` })
    );

    return wrap;
  }

  function deployBlock(it) {
    const me = state.me;
    const maxHours = me && me.maxLifetimeHours ? me.maxLifetimeHours : 48;
    const now = new Date();
    const defaultEnd = new Date(now.getTime() + Math.min(48, maxHours) * 3600 * 1000);
    const maxEnd = new Date(now.getTime() + maxHours * 3600 * 1000);

    const envSelect = el("select", { class: "deploy-input", id: "deploy-env" }, [
      el("option", { value: "dev", text: "dev" }),
    ]);
    if (it.hasProd) envSelect.appendChild(el("option", { value: "prod", text: "prod" }));

    const endInput = el("input", {
      type: "datetime-local",
      class: "deploy-input",
      id: "deploy-end",
      value: localDatetimeValue(defaultEnd),
      min: localDatetimeValue(new Date(now.getTime() + 15 * 60 * 1000)),
    });
    if (me && !me.isAdmin) endInput.setAttribute("max", localDatetimeValue(maxEnd));

    const status = el("div", { class: "deploy-status", role: "status" });
    const btn = el("button", { class: "deploy-btn", type: "button", text: "Deploy to my subscription" });

    const lifeNote = me && me.isAdmin
      ? "As an admin you can set any end time. Resources are auto-deleted at the end time."
      : `Pick when this should be torn down (max ${maxHours}h). Resources are auto-deleted at the end time.`;

    if (!me) {
      status.textContent = "Sign in to deploy from the gallery.";
      btn.disabled = true;
    }

    btn.addEventListener("click", () => {
      status.classList.remove("err", "ok");
      const endVal = endInput.value;
      if (!endVal) {
        status.textContent = "Pick an end time first.";
        status.classList.add("err");
        return;
      }
      const payload = {
        useCase: it.path,
        environment: envSelect.value,
        endAt: new Date(endVal).toISOString(),
      };
      btn.disabled = true;
      status.textContent = "Requesting deployment…";
      window.TechlabsApi.createDeployment(payload)
        .then((dep) => {
          status.classList.add("ok");
          status.innerHTML = "";
          status.appendChild(
            el("span", { text: `Queued (${dep.status}). Resource group ${dep.resourceGroup}. ` })
          );
          status.appendChild(
            el("a", { href: dep.ghRunUrl || "", target: "_blank", rel: "noopener", text: "View in Azure portal →" })
          );
        })
        .catch((err) => {
          status.classList.add("err");
          status.textContent = err && err.message ? err.message : "Deployment request failed.";
          btn.disabled = false;
        });
    });

    const form = el("div", { class: "deploy-form" }, [
      el("label", { class: "deploy-label", text: "Environment" }),
      envSelect,
      el("label", { class: "deploy-label", text: "End time (auto-cleanup)" }),
      endInput,
      el("p", { class: "deploy-note", text: lifeNote }),
      btn,
      status,
    ]);

    const manual = el("details", { class: "deploy-manual" }, [
      el("summary", { text: "Or deploy manually with the Azure CLI" }),
      el("div", {
        class: "code",
        text: `az group create --name rg-techlabs --location eastus\naz deployment group create \`\n  --resource-group rg-techlabs \`\n  --template-file ${it.template} \`\n  --parameters catalog/${it.path}/parameters/dev.bicepparam`,
      }),
    ]);

    return el("div", { class: "modal-section" }, [el("h3", { text: "Deploy" }), form, manual]);
  }

  function openModal(it) {
    if (window.TechlabsApi) window.TechlabsApi.recordEvent("view", it.path);
    const body = $("#modal-body");
    body.innerHTML = "";
    body.appendChild(el("span", { class: "card-cat", text: it.category || "Other" }));
    body.appendChild(el("h2", { text: it.displayName }));

    const sections = [];
    if (it.what) sections.push(["What it is", el("p", { text: it.what })]);
    if (it.why) sections.push(["Why it exists", el("p", { text: it.why })]);
    if (it.potentialUseCases && it.potentialUseCases.length)
      sections.push(["Potential use cases", el("ul", {}, it.potentialUseCases.map((u) => el("li", { text: u })))]);

    sections.forEach(([title, content]) => {
      body.appendChild(el("div", { class: "modal-section" }, [el("h3", { text: title }), content]));
    });

    const pillars = pillarBlock(it.wellArchitected);
    if (pillars)
      body.appendChild(el("div", { class: "modal-section" }, [el("h3", { text: "Well-Architected" }), pillars]));

    body.appendChild(deployBlock(it));

    const cost = costBlock(it);
    if (cost) body.appendChild(cost);

    body.appendChild(
      el("div", { class: "modal-section" }, [
        el("a", { href: `${REPO}/tree/main/catalog/${it.path}`, target: "_blank", rel: "noopener", text: "View on GitHub →" }),
      ])
    );

    $("#modal").hidden = false;
  }

  function closeModal() {
    $("#modal").hidden = true;
  }

  function wireModal() {
    document.querySelectorAll("[data-close]").forEach((n) => n.addEventListener("click", closeModal));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });
  }

  function init(catalog) {
    state.items = catalog.items || [];
    buildFilter("filter-category", uniqueScalar("category"), state.category);
    buildFilter("filter-industry", uniqueValues("industries"), state.industry);
    buildFilter("filter-compliance", uniqueValues("compliance"), state.compliance);

    $("#search").addEventListener("input", (e) => {
      state.q = e.target.value.trim().toLowerCase();
      render();
    });
    $("#clear-filters").addEventListener("click", () => {
      state.category.clear();
      state.industry.clear();
      state.compliance.clear();
      state.q = "";
      $("#search").value = "";
      document.querySelectorAll(".filter-options input").forEach((c) => (c.checked = false));
      render();
    });

    wireModal();
    render();
  }

  function loadIdentity() {
    if (!window.TechlabsApi) return;
    window.TechlabsApi.me()
      .then((me) => {
        state.me = me;
        if (me && me.isAdmin) {
          const link = document.getElementById("nav-admin");
          if (link) link.hidden = false;
        }
        document.dispatchEvent(new CustomEvent("techlabs:me", { detail: me }));
      })
      .catch(() => {
        state.me = null;
      });
  }

  loadIdentity();

  fetch("catalog.json")
    .then((r) => {
      if (!r.ok) throw new Error("Failed to load catalog.json");
      return r.json();
    })
    .then(init)
    .catch((err) => {
      $("#cards").appendChild(
        el("p", { class: "card-desc", text: "Could not load catalog.json. Run ./tools/Build-Catalog.ps1 to generate it." })
      );
      console.error(err);
    });
})();
