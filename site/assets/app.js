/* SME&C Techlabs gallery - loads catalog.json and renders filterable cards. */
(function () {
  "use strict";

  const REPO = "https://github.com/mcaps-microsoft/TechLabs";
  const state = { items: [], q: "", category: new Set(), industry: new Set(), compliance: new Set(), me: null, capacity: null, updates: null };

  // Connector arrow drawn between architecture columns (theme accent).
  const ARROW_SVG =
    '<svg viewBox="0 0 32 16" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">' +
    '<path d="M2 8h24M22 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // Capacity status ranking: green is best, red is worst. unknown/yellow are
  // treated as the same middle "caution" tier when rolling up a use case.
  const CAP_RANK = { green: 0, unknown: 1, yellow: 1, red: 2 };
  const ROLL_LABEL = {
    green: "Can host this use case",
    yellow: "May hit capacity constraints",
    red: "No capacity for at least one service",
    unknown: "Capacity unknown",
  };
  function worseStatus(a, b) {
    const ra = CAP_RANK[a] === undefined ? 1 : CAP_RANK[a];
    const rb = CAP_RANK[b] === undefined ? 1 : CAP_RANK[b];
    return rb > ra ? b : a;
  }
  // Map a use-case quota requirement to a key in capacity.json's services map.
  function capacityServiceKey(req) {
    if (!req) return null;
    if (req.kind === "model-tpm" && req.model) return "openai:" + req.model;
    const p = (req.provider || "").toLowerCase();
    if (p.indexOf("microsoft.web/serverfarms") === 0) return "appservice";
    if (p.indexOf("microsoft.compute/virtualmachines") === 0) return "vm";
    if (p.indexOf("microsoft.app/managedenvironments") === 0) return "containerapps";
    if (p.indexOf("microsoft.avs/privateclouds") === 0) return "avs";
    return null;
  }
  // Roll up every quota requirement of a use case to a single status for one
  // region, plus a per-service breakdown.
  function regionStatus(it, region) {
    const cap = state.capacity;
    const reqs = (it.quota && it.quota.requirements) || [];
    const services = reqs.map((req) => {
      const key = capacityServiceKey(req);
      let status = "unknown";
      let detail = "No live capacity signal for this service.";
      if (cap && key && cap.services && cap.services[key] && cap.services[key].byRegion[region]) {
        const e = cap.services[key].byRegion[region];
        status = e.status;
        detail = e.detail || detail;
      }
      return { key: key, label: req.service, status: status, detail: detail };
    });
    let roll = services.length ? "green" : "unknown";
    services.forEach((s) => (roll = worseStatus(roll, s.status)));
    return { status: roll, services: services };
  }
  function capacityRegions() {
    const cap = state.capacity;
    return cap && cap.regions && cap.regions.length ? cap.regions : ["centralus"];
  }

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
      cardAttention(it),
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

  // Small badge on cards whose services may be quota/capacity constrained,
  // or that include a service with an upcoming Azure retirement.
  function cardAttention(it) {
    const notes = [];
    const reqs = it.quota && it.quota.requirements;
    if (reqs && reqs.length) {
      notes.push(
        el("div", { class: "card-attention", title: it.quota.summary || "" }, [
          el("span", { class: "card-attention-icon", text: "\u26A0", "aria-hidden": "true" }),
          el("span", { text: "Quota-sensitive \u2014 check region capacity" }),
        ])
      );
    }
    const life = lifecycleForUseCase(it);
    if (life.retirements.length) {
      const n = life.retirements.length;
      notes.push(
        el("div", { class: "card-attention card-attention-retire", title: life.retirements[0].title }, [
          el("span", { class: "card-attention-icon", text: "\u26A0", "aria-hidden": "true" }),
          el("span", { text: n + " Azure retirement" + (n > 1 ? "s" : "") + " affecting this use case" }),
        ])
      );
    }
    if (!notes.length) return null;
    return el("div", { class: "card-attention-group" }, notes);
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

    const wrap = el("div", { class: "modal-section modal-card" }, [el("h3", { text: "Cost estimate" })]);

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

  function pollDeployment(dep, status, btn) {
    const TERMINAL = { active: 1, failed: 1, destroyed: 1 };
    // Status words → colour class (green done, red failed, accent in-progress).
    const STATUS_CLASS = {
      ready: "s-ok", succeeded: "s-ok", active: "s-ok", deployed: "s-ok", complete: "s-ok",
      failed: "s-err", canceled: "s-err", cancelled: "s-err", error: "s-err",
      provisioning: "s-run", creating: "s-run", queued: "s-run", deploying: "s-run",
      requested: "s-run", destroying: "s-run", deleting: "s-run", pending: "s-run",
    };
    function colorize(text) {
      const frag = document.createDocumentFragment();
      // Split on whole words, keeping delimiters, so each status token can be wrapped.
      String(text).split(/(\b[A-Za-z]+\b)/).forEach((part) => {
        const cls = STATUS_CLASS[part.toLowerCase()];
        if (cls) frag.appendChild(el("span", { class: cls, text: part }));
        else if (part) frag.appendChild(document.createTextNode(part));
      });
      return frag;
    }

    status.classList.remove("err", "ok");
    status.innerHTML = "";
    const head = el("div", { class: "deploy-log-head" });
    const log = el("div", { class: "deploy-log" });
    status.appendChild(head);
    status.appendChild(log);
    const seen = {};

    function logLine(text) {
      const ts = new Date().toLocaleTimeString();
      const line = el("div", { class: "deploy-log-line" });
      line.appendChild(el("span", { class: "deploy-log-ts", text: `[${ts}] ` }));
      line.appendChild(colorize(text));
      log.appendChild(line);
      log.scrollTop = log.scrollHeight;
    }
    function setHead(d) {
      head.innerHTML = "";
      head.appendChild(document.createTextNode("Status: "));
      head.appendChild(colorize(d.status));
      head.appendChild(document.createTextNode(` · resource group ${d.resourceGroup} · `));
      head.appendChild(
        el("a", { href: d.ghRunUrl || "", target: "_blank", rel: "noopener", text: "View in Azure portal →" })
      );
    }
    function note(d) {
      const key = `${d.status}|${d.detail || ""}`;
      if (seen[key]) return;
      seen[key] = 1;
      logLine(d.detail ? `${d.status} — ${d.detail}` : d.status);
    }

    setHead(dep);
    note(dep);

    const timer = setInterval(() => {
      window.TechlabsApi.getDeployment(dep.id)
        .then((d) => {
          setHead(d);
          note(d);
          if (TERMINAL[d.status]) {
            clearInterval(timer);
            status.classList.add(d.status === "active" ? "ok" : "err");
            if (d.status === "active") {
              logLine("complete — all resources provisioned and ready.");
            } else if (d.status === "failed") {
              logLine("failed — see the error above or open the resource group in the Azure portal.");
            }
            if (d.status !== "active") btn.disabled = false;
          }
        })
        .catch(() => {
          /* transient errors (e.g. session refresh) — keep polling */
        });
    }, 3000);
  }

  // Color-coded region picker reflecting live capacity for a use case.
  function deployRegionField(it) {
    const regions = capacityRegions();
    const select = el("select", { class: "deploy-input deploy-region", id: "deploy-region" });
    let preferred = null;
    regions.forEach((r) => {
      const rs = regionStatus(it, r);
      const opt = el("option", { value: r, text: `${r} \u2014 ${ROLL_LABEL[rs.status]}` });
      opt.setAttribute("data-status", rs.status);
      select.appendChild(opt);
      if (!preferred && rs.status === "green") preferred = r;
    });
    if (preferred) select.value = preferred;

    const note = el("div", { class: "deploy-region-note" });
    function refresh() {
      const r = select.value;
      const rs = regionStatus(it, r);
      select.setAttribute("data-status", rs.status);
      note.className = "deploy-region-note cap-note-" + rs.status;
      note.innerHTML = "";
      note.appendChild(
        el("div", { class: "cap-summary" }, [
          el("span", { class: "cap-dot cap-" + rs.status }),
          el("span", { text: ` ${ROLL_LABEL[rs.status]} in ${r}.` }),
        ])
      );
      if (rs.services.length) {
        note.appendChild(
          el(
            "ul",
            { class: "cap-svc-list" },
            rs.services.map((s) =>
              el("li", {}, [
                el("span", { class: "cap-dot cap-" + s.status }),
                el("span", { text: ` ${s.label} \u2014 ${s.detail}` }),
              ])
            )
          )
        );
      }
      if (rs.status === "red") {
        note.appendChild(
          el("div", {
            class: "deploy-region-hint",
            text: "At least one required service has no capacity here. Pick a greener region, or split the workload across regions (multi-region deployment).",
          })
        );
      }
    }
    select.addEventListener("change", refresh);
    refresh();
    return { select: select, note: note };
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

    const region = deployRegionField(it);

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
        location: region.select.value,
        endAt: new Date(endVal).toISOString(),
      };
      btn.disabled = true;
      status.textContent = "Requesting deployment…";
      window.TechlabsApi.createDeployment(payload)
        .then((dep) => {
          status.classList.add("ok");
          pollDeployment(dep, status, btn);
        })
        .catch((err) => {
          status.classList.add("err");
          status.textContent = err && err.message ? err.message : "Deployment request failed.";
          btn.disabled = false;
        });
    });

    const form = el("div", { class: "deploy-form" }, [
      el("label", { class: "deploy-label", text: "Region" }),
      region.select,
      region.note,
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
        text: `az group create --name rg-techlabs --location centralus\naz deployment group create \`\n  --resource-group rg-techlabs \`\n  --template-file ${it.template} \`\n  --parameters catalog/${it.path}/parameters/dev.bicepparam`,
      }),
    ]);

    return el("div", { class: "modal-section modal-card" }, [el("h3", { text: "Deploy" }), form, manual]);
  }

  // Capacity & quota notice for the use-case detail modal.
  function quotaBlock(it) {
    const q = it.quota;
    if (!q || !(q.requirements && q.requirements.length)) return null;
    const wrap = el("div", { class: "modal-section modal-card" }, [el("h3", { text: "Capacity & quota" })]);
    if (q.summary) wrap.appendChild(el("p", { text: q.summary }));

    const list = el("div", { class: "quota-list" });
    q.requirements.forEach((req) => {
      list.appendChild(
        el("div", { class: "quota-item" }, [
          el("div", { class: "quota-item-head" }, [
            el("span", { class: "quota-item-name", text: req.service }),
            req.sku ? el("span", { class: "quota-tag", text: req.sku }) : null,
          ]),
          el("p", { class: "quota-item-detail", text: req.detail || "" }),
          req.checkUrl
            ? el("a", { href: req.checkUrl, target: "_blank", rel: "noopener", text: "Check quota \u2192" })
            : null,
        ])
      );
    });
    wrap.appendChild(list);

    const link = el("button", { class: "link-btn", type: "button", text: "View full region capacity \u2192" });
    link.addEventListener("click", openCapacityPanel);
    wrap.appendChild(link);
    return wrap;
  }

  // A single service tile: official-style Azure icon + label/sublabel.
  function archNode(node) {
    const icons = window.AzureIcons;
    const svg = icons ? icons.iconFor(node.icon) : "";
    return el("div", { class: "arch-node" }, [
      el("span", { class: "arch-icon", html: svg }),
      el("span", { class: "arch-node-text" }, [
        el("span", { class: "arch-node-label", text: node.label || node.id }),
        node.sublabel ? el("span", { class: "arch-node-sub", text: node.sublabel }) : null,
      ]),
    ]);
  }

  // Render a flow of nodes grouped into columns (by node.col) with arrow
  // connectors between adjacent columns to convey request/data flow. When
  // `vertical` is set the tiers stack top-to-bottom (used in the side panel).
  function renderArchFlow(nodes, vertical) {
    const byCol = {};
    nodes.forEach((n) => {
      const c = typeof n.col === "number" ? n.col : 0;
      (byCol[c] = byCol[c] || []).push(n);
    });
    const cols = Object.keys(byCol).map(Number).sort((a, b) => a - b);
    const flow = el("div", { class: vertical ? "arch-flow arch-flow-vertical" : "arch-flow" });
    cols.forEach((c, idx) => {
      flow.appendChild(el("div", { class: "arch-col" }, byCol[c].map(archNode)));
      if (idx < cols.length - 1) {
        flow.appendChild(el("div", { class: "arch-conn", "aria-hidden": "true", html: ARROW_SVG }));
      }
    });
    return el("div", { class: "arch-scroll" }, [flow]);
  }

  // Architecture diagram for the use-case modal. Uses an explicit node/edge
  // graph when the use case declares one, otherwise derives a tiered service
  // flow from the cost components. Always renders official-style Azure icons.
  function architectureBlock(it) {
    const icons = window.AzureIcons;
    const arch = it.architecture;
    const wrap = el("div", { class: "modal-section arch-block" }, [el("h3", { text: "Architecture" })]);

    if (arch && arch.nodes && arch.nodes.length) {
      if (arch.summary) wrap.appendChild(el("p", { class: "arch-summary", text: arch.summary }));
      wrap.appendChild(renderArchFlow(arch.nodes, true));

      const edges = (arch.edges || []).filter((e) => e.from && e.to);
      if (edges.length) {
        const labelOf = (id) => {
          const n = arch.nodes.find((x) => x.id === id);
          return n ? n.label || n.id : id;
        };
        wrap.appendChild(
          el(
            "ul",
            { class: "arch-edges" },
            edges.map((e) =>
              el("li", {}, [
                el("span", { text: labelOf(e.from) }),
                el("span", { class: "arch-edge-arrow", text: " \u2192 " }),
                el("span", { text: labelOf(e.to) }),
                e.label ? el("span", { class: "arch-edge-label", text: " \u00b7 " + e.label }) : null,
              ])
            )
          )
        );
      }
      return wrap;
    }

    // Fallback: derive a tiered flow (Users -> edge -> compute -> data/obs)
    // from the distinct Azure services this use case provisions.
    if (!icons) return null;
    const comps = (it.cost && it.cost.components) || [];
    const seen = {};
    const services = [];
    comps.forEach((c) => {
      const name = c.serviceName;
      if (!name) return;
      const key = name.toLowerCase();
      if (seen[key]) return;
      seen[key] = 1;
      services.push(name);
    });
    if (!services.length) return null;

    // Column index per tier; everything sits to the right of the Users node.
    const TIER_COL = { edge: 1, compute: 2, data: 3, obs: 3 };
    const nodes = [
      { id: "user", label: "Users", sublabel: "HTTPS clients", icon: "client", col: 0 },
    ];
    services.forEach((name, i) => {
      const tier = icons.tierForService(name);
      nodes.push({
        id: "n" + i,
        label: name,
        icon: icons.keyForService(name),
        col: tier === "client" ? 1 : TIER_COL[tier] || 2,
      });
    });

    wrap.appendChild(
      el("p", { class: "arch-summary", text: "Request and data flow across the Azure services this use case provisions:" })
    );
    wrap.appendChild(renderArchFlow(nodes, true));
    return wrap;
  }

  // Distinct catalog icon-keys for the services a use case provisions, derived
  // from its cost-meter component names (shared vocabulary with updates.json).
  function useCaseServiceKeys(it) {
    const icons = window.AzureIcons;
    const comps = (it.cost && it.cost.components) || [];
    const keys = [];
    const seen = {};
    comps.forEach((c) => {
      const key = icons ? icons.keyForService(c.serviceName) : null;
      if (!key || key === "default" || seen[key]) return;
      seen[key] = 1;
      keys.push(key);
    });
    return keys;
  }

  // Collect lifecycle notices (retirements / previews / recent GA) for every
  // service a use case uses, de-duplicated by update id within each bucket.
  function lifecycleForUseCase(it) {
    const up = state.updates;
    if (!up || !up.services) return { retirements: [], previews: [], launched: [] };
    const out = { retirements: [], previews: [], launched: [] };
    const seen = { retirements: {}, previews: {}, launched: {} };
    useCaseServiceKeys(it).forEach((key) => {
      const svc = up.services[key];
      if (!svc) return;
      ["retirements", "previews", "launched"].forEach((bucket) => {
        (svc[bucket] || []).forEach((e) => {
          if (seen[bucket][e.id]) return;
          seen[bucket][e.id] = 1;
          out[bucket].push(e);
        });
      });
    });
    return out;
  }

  // Format a "YYYY-MM" string as "Mon YYYY"; pass through anything else.
  function fmtMonth(ym) {
    if (!ym || !/^\d{4}-\d{2}$/.test(ym)) return null;
    const d = new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1, 1);
    return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
  }

  // Boxed modal section listing Azure roadmap signals for this use case's
  // services: upcoming retirements first (action needed), then new previews
  // and recent GA so architectures can be kept current.
  function lifecycleBlock(it) {
    const life = lifecycleForUseCase(it);
    const total = life.retirements.length + life.previews.length + life.launched.length;
    if (!total) return null;

    const wrap = el("div", { class: "modal-section modal-card lifecycle-block" }, [
      el("h3", { text: "Azure roadmap & retirements" }),
    ]);
    wrap.appendChild(
      el("p", {
        class: "lifecycle-sub",
        text: "Lifecycle changes for the Azure services this use case uses. Review retirements before they take effect.",
      })
    );

    const groups = [
      ["retirements", "Retiring", "lc-retire", "\u26A0"],
      ["previews", "In preview", "lc-preview", "\u2728"],
      ["launched", "Recently GA", "lc-ga", "\u2714"],
    ];
    groups.forEach(([bucket, label, cls, glyph]) => {
      const items = life[bucket];
      if (!items.length) return;
      const list = el("ul", { class: "lifecycle-list" });
      items.slice(0, 6).forEach((e) => {
        const when = fmtMonth(e.date);
        list.appendChild(
          el("li", { class: "lifecycle-item " + cls }, [
            el("span", { class: "lifecycle-badge " + cls }, [
              el("span", { class: "lifecycle-badge-icon", text: glyph, "aria-hidden": "true" }),
              el("span", { text: label }),
            ]),
            el("a", { class: "lifecycle-title", href: e.url, target: "_blank", rel: "noopener", text: e.title }),
            when ? el("span", { class: "lifecycle-date", text: when }) : null,
          ])
        );
      });
      wrap.appendChild(list);
    });
    return wrap;
  }

  function openModal(it) {
    if (window.TechlabsApi) window.TechlabsApi.recordEvent("view", it.path);
    const body = $("#modal-body");
    body.innerHTML = "";
    body.appendChild(el("span", { class: "card-cat", text: it.category || "Other" }));
    body.appendChild(el("h2", { text: it.displayName }));

    // Row 1: narrative (left) + architecture diagram (right).
    const narrative = el("div", { class: "modal-col-main" });
    const sections = [];
    if (it.what) sections.push(["What it is", el("p", { text: it.what })]);
    if (it.why) sections.push(["Why it exists", el("p", { text: it.why })]);
    if (it.potentialUseCases && it.potentialUseCases.length)
      sections.push(["Potential use cases", el("ul", {}, it.potentialUseCases.map((u) => el("li", { text: u })))]);
    sections.forEach(([title, content]) => {
      narrative.appendChild(el("div", { class: "modal-section" }, [el("h3", { text: title }), content]));
    });

    const arch = architectureBlock(it);
    const row1 = el("div", { class: arch ? "modal-grid" : "modal-grid modal-grid-single" }, [narrative]);
    if (arch) row1.appendChild(el("div", { class: "modal-col-side" }, [arch]));
    body.appendChild(row1);

    // Row 2: Well-Architected + Capacity & quota, side by side.
    const pillars = pillarBlock(it.wellArchitected);
    const waCard = pillars
      ? el("div", { class: "modal-section modal-card" }, [el("h3", { text: "Well-Architected" }), pillars])
      : null;
    const quota = quotaBlock(it);
    const row2 = [];
    if (waCard) row2.push(waCard);
    if (quota) row2.push(quota);
    if (row2.length) {
      const cls = row2.length === 2 ? "modal-grid modal-grid-even" : "modal-grid modal-grid-single";
      body.appendChild(el("div", { class: cls }, row2));
    }

    // Azure roadmap & retirements (boxed) — keeps the architecture current.
    const lifecycle = lifecycleBlock(it);
    if (lifecycle) body.appendChild(lifecycle);

    // Deploy section (boxed).
    body.appendChild(deployBlock(it));

    // Cost estimate (boxed).
    const cost = costBlock(it);
    if (cost) body.appendChild(cost);

    body.appendChild(
      el("div", { class: "modal-section modal-foot" }, [
        el("a", { href: `${REPO}/tree/main/catalog/${it.path}`, target: "_blank", rel: "noopener", text: "View on GitHub →" }),
      ])
    );

    $("#modal").hidden = false;
  }

  function closeModal() {
    $("#modal").hidden = true;
  }

  // Full-screen "Region capacity" view: a service x region status grid built
  // from the static capacity.json snapshot. Reuses the shared panel overlay.
  function openCapacityPanel() {
    if (!window.TechlabsPanel) return;
    closeModal();
    window.TechlabsPanel.open((bodyEl, e) => {
      bodyEl.appendChild(e("h2", { text: "Region capacity" }));
      const cap = state.capacity;
      if (!cap || !cap.services) {
        bodyEl.appendChild(
          e("p", { text: "Capacity data is not available yet. It is refreshed daily by tools/Build-Capacity.ps1." })
        );
        return;
      }
      bodyEl.appendChild(
        e("p", {
          class: "cap-generated",
          text:
            "Snapshot of subscription quota and regional availability for capacity-sensitive services. Last updated " +
            new Date(cap.generated).toLocaleString() +
            ".",
        })
      );

      const legend = e("div", { class: "cap-legend" });
      ["green", "yellow", "red", "unknown"].forEach((s) => {
        if (!cap.legend || !cap.legend[s]) return;
        legend.appendChild(
          e("span", { class: "cap-legend-item" }, [
            e("span", { class: "cap-dot cap-" + s }),
            e("span", { text: " " + cap.legend[s] }),
          ])
        );
      });
      bodyEl.appendChild(legend);

      const regions = cap.regions || [];
      const headRow = e(
        "tr",
        {},
        [e("th", { text: "Service" })].concat(regions.map((r) => e("th", { text: r })))
      );
      const rows = Object.keys(cap.services).map((key) => {
        const svc = cap.services[key];
        const cells = [e("td", { class: "cap-svc-name", text: svc.label || key })];
        regions.forEach((r) => {
          const cell = (svc.byRegion && svc.byRegion[r]) || { status: "unknown", detail: "" };
          cells.push(
            e("td", { class: "cap-cell", title: cell.detail || "" }, [
              e("span", { class: "cap-dot cap-" + cell.status }),
            ])
          );
        });
        return e("tr", {}, cells);
      });
      bodyEl.appendChild(
        e("div", { class: "cap-table-wrap" }, [
          e("table", { class: "cap-table" }, [e("thead", {}, headRow), e("tbody", {}, rows)]),
        ])
      );
    });
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

  function wireCapacityNav() {
    const btn = $("#nav-capacity");
    if (btn) btn.addEventListener("click", openCapacityPanel);
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
  wireCapacityNav();

  // Capacity snapshot is best-effort: the gallery still works without it
  // (the region picker just falls back to a single default region).
  fetch("capacity.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((c) => {
      state.capacity = c;
    })
    .catch(() => {
      /* no capacity data — degrade gracefully */
    });

  // Azure service lifecycle snapshot (retirements / previews / recent GA),
  // refreshed daily by tools/Build-Updates.ps1. Also best-effort.
  fetch("updates.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((u) => {
      state.updates = u;
      if (u) render();
    })
    .catch(() => {
      /* no updates data — gallery hides lifecycle notices */
    });

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
