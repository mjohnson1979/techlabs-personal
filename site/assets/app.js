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

  // Persist a <details> open/closed state for the current tab session.
  // Groups default to collapsed (no `open` in markup) until the user toggles them.
  function persistDetails(detailsEl, key) {
    let saved = null;
    try {
      saved = sessionStorage.getItem(key);
    } catch (e) {
      saved = null;
    }
    if (saved === "open") detailsEl.open = true;
    else if (saved === "closed") detailsEl.open = false;
    detailsEl.addEventListener("toggle", () => {
      try {
        sessionStorage.setItem(key, detailsEl.open ? "open" : "closed");
      } catch (e) {
        /* sessionStorage unavailable (private mode) - non-fatal */
      }
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
    if (Array.isArray(it.engines) && it.engines.indexOf("terraform") !== -1) {
      chips.push(el("span", { class: "chip iac", text: "Bicep + Terraform" }));
    }
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

  // Small badge on cards whose services may be quota/capacity constrained.
  // Retirement notices are intentionally NOT shown on cards (they live in the
  // use-case modal and on the dedicated Roadmap & retirements page).
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

  // A <details> "card" used for collapsible modal sections. Defaults to
  // collapsed; pass open=true to start expanded.
  function collapsibleCard(titleText, content, open) {
    const details = el("details", { class: "modal-section modal-card collapsible-card" }, [
      el("summary", { class: "collapse-summary" }, [
        el("span", { class: "collapse-title", role: "heading", "aria-level": "3", text: titleText }),
        el("span", { class: "collapse-caret", "aria-hidden": "true" }),
      ]),
    ]);
    if (open) details.setAttribute("open", "");
    (Array.isArray(content) ? content : [content]).forEach((n) => n && details.appendChild(n));
    return details;
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

    const totalMonthly = typeof cost.totalMonthly === "number" ? cost.totalMonthly : null;
    const totalMoney = totalMonthly !== null ? fmtMoney(totalMonthly, cost.currency) : null;
    const summaryTotal =
      totalMoney && totalMonthly > 0
        ? `for the selected services: ~${totalMoney}/mo`
        : "for the selected services: usage-based";

    const wrap = el("details", { class: "modal-section modal-card collapsible-card" }, [
      el("summary", { class: "collapse-summary" }, [
        el("span", { class: "cost-summary-lead" }, [
          el("span", { class: "collapse-title", role: "heading", "aria-level": "3", text: "Cost estimate" }),
          el("span", { class: "cost-summary-total", text: summaryTotal }),
        ]),
        el("span", { class: "collapse-caret", "aria-hidden": "true" }),
      ]),
    ]);

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

  function parseOverrides(raw) {
    return String(raw || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#") && !line.startsWith("//") && line.includes("="));
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

    const engines = Array.isArray(it.engines) && it.engines.length ? it.engines : ["bicep"];
    const hasTerraform = engines.indexOf("terraform") !== -1;
    const engineSelect = el("select", { class: "deploy-input", id: "deploy-engine" }, [
      el("option", { value: "bicep", text: "Bicep (ARM)" }),
    ]);
    if (hasTerraform) engineSelect.appendChild(el("option", { value: "terraform", text: "Terraform" }));

    const rgInput = el("input", {
      type: "text",
      class: "deploy-input",
      id: "deploy-rg",
      value: `rg-techlabs-${it.path}`,
      placeholder: "rg-techlabs",
    });

    const overrideInput = el("textarea", {
      class: "deploy-input deploy-overrides",
      id: "deploy-overrides",
      placeholder: "Optional parameter overrides, one per line\nexample:\nprefix=contoso\nenvironment=mvp",
    });

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

    const tfExec = !!(me && me.terraformExecAvailable);
    const engineHint = el("p", { class: "deploy-note deploy-engine-hint", text: "" });

    function syncEngine() {
      manualCode.textContent = manualSnippet(engineSelect.value);
      if (!me) return;
      if (engineSelect.value === "terraform" && !tfExec) {
        btn.disabled = true;
        engineHint.textContent =
          "In-app Terraform deploy isn't enabled on this server yet - copy the CLI below to run it locally.";
        engineHint.style.display = "";
      } else {
        btn.disabled = false;
        engineHint.textContent = "";
        engineHint.style.display = "none";
      }
    }

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
        engine: engineSelect.value,
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

    const deployField = (labelText, control, extra) =>
      el("div", { class: "deploy-field" }, [
        el("label", { class: "deploy-label", for: control.id, text: labelText }),
        control,
        extra || null,
      ]);

    const form = el("div", { class: "deploy-form" }, [
      deployField("Region", region.select, region.note),
      deployField("Environment", envSelect),
      deployField("IaC engine", engineSelect, engineHint),
      deployField("Resource group", rgInput),
    ]);

    const advancedForm = el("div", { class: "deploy-form deploy-advanced-form" }, [
      el("label", { class: "deploy-label", for: "deploy-overrides", text: "Template parameter overrides" }),
      overrideInput,
      el("label", { class: "deploy-label", for: "deploy-end", text: "End time (auto-cleanup)" }),
      endInput,
      el("p", { class: "deploy-note", text: lifeNote }),
    ]);

    function manualSnippet(engine) {
      const env = envSelect.value;
      const location = region.select.value;
      const rg = (rgInput.value || "rg-techlabs").trim();
      const overrides = parseOverrides(overrideInput.value);

      if (engine === "terraform") {
        const tfArgs = [
          `-var-file=${env}.tfvars`,
          `-var resource_group_name=${rg}`,
          `-var location=${location}`,
        ];
        overrides.forEach((line) => {
          const i = line.indexOf("=");
          if (i < 1) return;
          const key = line.slice(0, i).trim();
          const value = line.slice(i + 1).trim();
          if (!key || !value) return;
          tfArgs.push(`-var ${key}=${value}`);
        });
        return [
          `cd catalog/${it.path}/terraform`,
          "terraform init",
          "terraform apply `\n  " + tfArgs.join(" `\n  "),
        ].join("\n");
      }

      const bicepArgs = [
        `--resource-group ${rg}`,
        `--template-file ${it.template}`,
        `--parameters catalog/${it.path}/parameters/${env}.bicepparam`,
      ];
      if (overrides.length) {
        bicepArgs.push(`--parameters ${overrides.join(" ")}`);
      }
      return [
        `az group create --name ${rg} --location ${location}`,
        "az deployment group create `\n  " + bicepArgs.join(" `\n  "),
      ].join("\n");
    }

    const manualCode = el("div", { class: "code", text: manualSnippet(engineSelect.value) });
    const manualSummary = el("summary", { text: "Or deploy manually with the CLI" });
    [engineSelect, envSelect, region.select, rgInput, overrideInput].forEach((control) => {
      control.addEventListener("change", syncEngine);
      control.addEventListener("input", syncEngine);
    });
    syncEngine();
    const manual = el("details", { class: "deploy-manual", hidden: "" }, [manualSummary, manualCode]);
    advancedForm.hidden = true;

    const footer = el("div", { class: "deploy-foot" }, [btn, status]);
    const card = el("div", { class: "modal-section modal-card deploy-card" });

    // Collapsed shows the basic fields + deploy button; expanding reveals the
    // parameter overrides, end time, and manual CLI (the full form).
    const header = el(
      "div",
      { class: "collapse-summary deploy-head", role: "button", tabindex: "0", "aria-expanded": "false" },
      [el("h3", { text: "Deploy" }), el("span", { class: "collapse-caret", "aria-hidden": "true" })]
    );

    let expanded = false;
    function toggleDeploy() {
      expanded = !expanded;
      advancedForm.hidden = !expanded;
      manual.hidden = !expanded;
      header.setAttribute("aria-expanded", String(expanded));
      card.classList.toggle("is-open", expanded);
    }
    header.addEventListener("click", toggleDeploy);
    header.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleDeploy();
      }
    });

    [header, form, advancedForm, footer, manual].forEach((n) => card.appendChild(n));
    return card;
  }

  // Capacity & quota notice for the use-case detail modal.
  function quotaBlock(it) {
    const q = it.quota;
    if (!q || !(q.requirements && q.requirements.length)) return null;
    const wrap = el("details", { class: "modal-section modal-card collapsible-card" }, [
      el("summary", { class: "collapse-summary" }, [
        el("span", { class: "collapse-title", role: "heading", "aria-level": "3", text: "Capacity & quota" }),
        el("span", { class: "collapse-caret", "aria-hidden": "true" }),
      ]),
    ]);
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

  // Boxed modal section focused on what matters for THIS use case: the upcoming
  // Azure retirements affecting its services (the actionable signal). The count
  // always equals the items listed, so it stays consistent. Previews / recent
  // GA and the full per-service catalog live on the Roadmap page, linked below.
  function lifecycleBlock(it) {
    const life = lifecycleForUseCase(it);
    const retire = sortedBucket("retirements", life.retirements);
    const hasOther = life.previews.length + life.launched.length > 0;
    if (!retire.length && !hasOther) return null;

    const n = retire.length;
    const wrap = el("details", { class: "modal-section modal-card lifecycle-block" }, [
      el("summary", { class: "lifecycle-summary" }, [
        el("h3", { text: "Azure retirements for this use case" }),
        el("span", { class: "lifecycle-count", text: String(n) }),
      ]),
    ]);
    wrap.appendChild(
      el("p", {
        class: "lifecycle-sub",
        text: n
          ? "Upcoming retirements affecting the Azure services this use case provisions. Review them before they take effect."
          : "No upcoming retirements affect this use case's services right now.",
      })
    );

    if (n) {
      const list = el("ul", { class: "lifecycle-list" });
      retire.forEach((e) => {
        const when = fmtMonth(e.date);
        list.appendChild(
          el("li", { class: "lifecycle-item lc-retire" }, [
            el("span", { class: "lifecycle-badge lc-retire" }, [
              el("span", { class: "lifecycle-badge-icon", text: "\u26A0", "aria-hidden": "true" }),
              el("span", { text: "Retiring" }),
            ]),
            el("a", { class: "lifecycle-title", href: e.url, target: "_blank", rel: "noopener", text: (e.title || "").trim() }),
            when ? el("span", { class: "lifecycle-date", text: when }) : null,
          ])
        );
      });
      wrap.appendChild(list);
    }

    const link = el("button", {
      type: "button",
      class: "lifecycle-roadmap-link",
      text: "Browse the full Azure roadmap & retirements \u2192",
    });
    link.addEventListener("click", openRoadmapPanel);
    wrap.appendChild(link);
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
    const waCard = pillars ? collapsibleCard("Well-Architected", pillars, false) : null;
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

  // ------------------------------------------------ Azure roadmap & retirements
  // Full-page browser for every Azure service lifecycle signal in updates.json:
  // search by service, collapsible per-service sections, grouped into upcoming
  // retirements (action needed), public previews, and recent GA. This is the
  // canonical, consistent place for the "big" lists; the per-use-case modal
  // only surfaces the retirements relevant to that use case and links here.
  var ROADMAP_GROUPS = [
    ["retirements", "Retiring", "lc-retire", "\u26A0"],
    ["previews", "In preview", "lc-preview", "\u2728"],
    ["launched", "Recently GA", "lc-ga", "\u2714"],
  ];

  function sortedBucket(bucket, items) {
    var arr = (items || []).slice();
    // Retirements: soonest first (act on these). Previews/GA: newest first.
    var asc = bucket === "retirements";
    arr.sort(function (a, b) {
      var da = a.date || "", db = b.date || "";
      if (da === db) return 0;
      return (da < db ? -1 : 1) * (asc ? 1 : -1);
    });
    return arr;
  }

  function openRoadmapPanel() {
    if (!window.TechlabsPanel) return;
    closeModal();
    var icons = window.AzureIcons;
    window.TechlabsPanel.open(function (bodyEl, e) {
      bodyEl.appendChild(e("h2", { text: "Azure roadmap & retirements" }));

      var up = state.updates;
      if (!up || !up.services) {
        bodyEl.appendChild(
          e("p", { text: "Roadmap data is not available yet. It is refreshed daily by tools/Build-Updates.ps1." })
        );
        return;
      }

      // Flatten the services map into a sortable list with per-bucket counts.
      var services = Object.keys(up.services).map(function (key) {
        var svc = up.services[key] || {};
        var buckets = {};
        var counts = {};
        var total = 0;
        ROADMAP_GROUPS.forEach(function (g) {
          var b = sortedBucket(g[0], svc[g[0]]);
          buckets[g[0]] = b;
          counts[g[0]] = b.length;
          total += b.length;
        });
        return {
          key: key,
          label: (icons && icons.labelForKey ? icons.labelForKey(key) : key),
          buckets: buckets,
          counts: counts,
          total: total,
        };
      }).filter(function (s) { return s.total > 0; });

      // Services with upcoming retirements float to the top (most retirements
      // first); the rest sort alphabetically for findability.
      services.sort(function (a, b) {
        if (a.counts.retirements !== b.counts.retirements) {
          return b.counts.retirements - a.counts.retirements;
        }
        return a.label.localeCompare(b.label);
      });

      var totalRetire = services.reduce(function (n, s) { return n + s.counts.retirements; }, 0);

      bodyEl.appendChild(
        e("p", {
          class: "cap-generated",
          text:
            services.length + " services with lifecycle changes \u00B7 " + totalRetire +
            " upcoming retirements. Sourced from Azure Updates, last refreshed " +
            (up.generated ? new Date(up.generated).toLocaleString() : "recently") + ".",
        })
      );

      // Controls: free-text search + group toggle chips.
      var search = e("input", {
        type: "search", class: "roadmap-search",
        placeholder: "Search by service (e.g. AKS, Storage, Functions)\u2026",
        "aria-label": "Search services",
      });
      var active = { retirements: true, previews: true, launched: true };
      var chipRow = e("div", { class: "roadmap-chips" });
      var chips = {};
      ROADMAP_GROUPS.forEach(function (g) {
        var chip = e("button", {
          type: "button", class: "roadmap-chip roadmap-chip-on " + g[2],
          text: g[1],
        });
        chips[g[0]] = chip;
        chip.addEventListener("click", function () {
          active[g[0]] = !active[g[0]];
          chip.classList.toggle("roadmap-chip-on", active[g[0]]);
          draw();
        });
        chipRow.appendChild(chip);
      });
      bodyEl.appendChild(e("div", { class: "roadmap-controls" }, [search, chipRow]));

      var listWrap = e("div", { class: "roadmap-list" });
      bodyEl.appendChild(listWrap);

      function draw() {
        listWrap.innerHTML = "";
        var q = search.value.trim().toLowerCase();
        var anyGroup = active.retirements || active.previews || active.launched;
        var shown = 0;
        services.forEach(function (s) {
          if (q && s.label.toLowerCase().indexOf(q) === -1 && s.key.indexOf(q) === -1) return;
          // Which groups will actually render for this service?
          var visibleGroups = ROADMAP_GROUPS.filter(function (g) {
            return active[g[0]] && s.counts[g[0]] > 0;
          });
          if (!visibleGroups.length) return;
          shown += 1;

          var countChips = visibleGroups.map(function (g) {
            return e("span", { class: "roadmap-count " + g[2], text: s.counts[g[0]] + " " + g[1].toLowerCase() });
          });
          var summary = e("summary", { class: "roadmap-svc-summary" }, [
            e("span", { class: "roadmap-svc-icon", html: (icons && icons.iconFor ? icons.iconFor(s.key) : ""), "aria-hidden": "true" }),
            e("span", { class: "roadmap-svc-name", text: s.label }),
            e("span", { class: "roadmap-svc-counts" }, countChips),
          ]);
          var details = e("details", { class: "roadmap-svc" }, [summary]);
          // Open the first few (those with retirements) by default.
          if (s.counts.retirements > 0 && shown <= 4) details.setAttribute("open", "");

          visibleGroups.forEach(function (g) {
            var list = e("ul", { class: "lifecycle-list" });
            s.buckets[g[0]].forEach(function (item) {
              var when = fmtMonth(item.date);
              list.appendChild(
                e("li", { class: "lifecycle-item " + g[2] }, [
                  e("span", { class: "lifecycle-badge " + g[2] }, [
                    e("span", { class: "lifecycle-badge-icon", text: g[3], "aria-hidden": "true" }),
                    e("span", { text: g[1] }),
                  ]),
                  e("a", {
                    class: "lifecycle-title", href: item.url, target: "_blank",
                    rel: "noopener", text: (item.title || "").trim(),
                  }),
                  when ? e("span", { class: "lifecycle-date", text: when }) : null,
                ])
              );
            });
            details.appendChild(list);
          });
          listWrap.appendChild(details);
        });

        if (!shown) {
          listWrap.appendChild(
            e("p", {
              class: "roadmap-empty",
              text: anyGroup ? "No services match your search." : "Select at least one category above.",
            })
          );
        }
      }

      search.addEventListener("input", draw);
      draw();
    });
  }

  // -------------------------------------------------------- submit a use case
  // Primary path: upload a ready-to-deploy template (zip with main.bicep +
  // parameters). The backend validates + stages it for a maintainer to review
  // and commit (see GitHub issue #6). The old "describe it in plain English"
  // flow is parked because AI-drafted Bicep is not deployable without a
  // maintainer finishing it.
  function openSubmitPanel() {
    if (!window.TechlabsPanel) return;
    closeModal();
    var Api = window.TechlabsApi;
    window.TechlabsPanel.open((bodyEl, e) => {
      bodyEl.appendChild(e("h2", { text: "Submit a use case" }));
      bodyEl.appendChild(
        e("p", {
          class: "submit-intro",
          text:
            "Already have a template? Upload a .zip containing your main.bicep and a parameters folder. We'll validate it and queue it for a maintainer to review and publish to the catalog.",
        })
      );

      const fileInput = e("input", {
        id: "submit-zip", class: "submit-input", type: "file", accept: ".zip,application/zip",
      });
      const name = e("input", {
        id: "submit-name", class: "submit-input", type: "text",
        placeholder: "Realtime IoT telemetry dashboard",
      });

      const form = e("div", { class: "submit-form submit-form-simple" }, [
        e("div", { class: "submit-field" }, [
          e("label", { class: "submit-label", for: "submit-zip", text: "Template zip *" }),
          e("span", {
            class: "submit-hint",
            text: "Required inside the zip: main.bicep (at the root) and at least one parameters/<env>.bicepparam. Optional: metadata.json, README.md.",
          }),
          fileInput,
        ]),
        e("div", { class: "submit-field" }, [
          e("label", { class: "submit-label", for: "submit-name", text: "Use case name (optional)" }),
          e("span", { class: "submit-hint", text: "Used to name the catalog entry. Leave blank to derive it from metadata.json." }),
          name,
        ]),
      ]);

      const status = e("div", { class: "submit-status", role: "status" });
      const submit = e("button", { class: "submit-btn", type: "button", text: "Upload template" });
      submit.addEventListener("click", () => {
        status.classList.remove("err", "ok");
        status.textContent = "";
        const file = fileInput.files && fileInput.files[0];
        if (!file) {
          status.classList.add("err");
          status.textContent = "Choose a .zip file to upload first.";
          return;
        }
        if (!/\.zip$/i.test(file.name)) {
          status.classList.add("err");
          status.textContent = "That doesn't look like a .zip archive.";
          return;
        }
        if (file.size > 5 * 1024 * 1024) {
          status.classList.add("err");
          status.textContent = "The zip is too large (5 MB limit).";
          return;
        }
        if (!Api || !Api.uploadUseCase) {
          status.classList.add("err");
          status.textContent = "Uploads are only available on the hosted gallery.";
          return;
        }
        submit.disabled = true;
        submit.textContent = "Uploading\u2026";
        Api.uploadUseCase(file, name.value.trim())
          .then((res) => {
            const sub = (res && res.submission) || {};
            status.classList.add("ok");
            status.innerHTML = "";
            status.appendChild(document.createTextNode(
              (res && res.detail) || "Uploaded. A maintainer will review it shortly."));
            if (sub.slug) {
              status.appendChild(e("div", { class: "submit-hint", text: "Catalog entry: " + sub.slug }));
            }
            submit.textContent = "Uploaded \u2713";
          })
          .catch((err) => {
            submit.disabled = false;
            submit.textContent = "Upload template";
            status.classList.add("err");
            status.textContent = (err && err.message) || "Upload failed.";
          });
      });

      bodyEl.appendChild(form);
      bodyEl.appendChild(submit);
      bodyEl.appendChild(status);

      bodyEl.appendChild(
        e("p", {
          class: "submit-intro submit-secondary",
          text:
            "Don't have the Bicep yet? The describe-it-in-plain-English flow is being reworked \u2014 for now, open a GitHub issue and a maintainer will help.",
        })
      );
      bodyEl.appendChild(
        e("a", {
          class: "link-btn", href: REPO + "/issues/new", target: "_blank",
          rel: "noopener", text: "Open a GitHub issue \u2192",
        })
      );
    });
  }

  function wireSubmitNav() {
    const btn = $("#submit-usecase");
    if (btn) btn.addEventListener("click", openSubmitPanel);
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

    document.querySelectorAll(".filter-group[data-filter-key]").forEach((d) => {
      persistDetails(d, "techlabs.filter." + d.getAttribute("data-filter-key"));
    });

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
    const rm = $("#nav-roadmap");
    if (rm) rm.addEventListener("click", openRoadmapPanel);
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
  wireSubmitNav();

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
