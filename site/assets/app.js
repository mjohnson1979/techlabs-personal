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
  // `sel` (advanced-option overrides) can swap the model so the picker reflects
  // capacity for the model the user actually chose.
  function openAiRegModels(isEmbed) {
    const reg = state.registry;
    if (!reg || !reg.models) return [];
    return ((isEmbed ? reg.models.embedding : reg.models.chat) || []).filter((m) => m.provider === "OpenAI");
  }
  function openAiDefaultVersion(model, isEmbed) {
    const m = openAiRegModels(isEmbed).find((x) => x.name === model);
    return m && m.version ? m.version : "";
  }
  function openAiDefaultSku(model, version, isEmbed) {
    const models = openAiRegModels(isEmbed);
    const m = models.find((x) => x.name === model && x.version === version) || models.find((x) => x.name === model);
    return m && Array.isArray(m.skus) && m.skus.length ? m.skus[0] : "Standard";
  }
  // Capacity key for an OpenAI deployment: model + version + SKU, matching the
  // keys Build-Capacity.ps1 emits. Falls back to any probe for the same model so
  // a version/SKU we did not probe still yields a model-level signal.
  function openAiKey(model, version, sku) {
    const cap = state.capacity;
    const full = "openai:" + model + ":" + (version || "") + ":" + String(sku || "").toLowerCase();
    if (cap && cap.services) {
      if (cap.services[full]) return full;
      const prefix = "openai:" + model + ":";
      const hit = Object.keys(cap.services).find((k) => k.indexOf(prefix) === 0);
      if (hit) return hit;
      // Legacy snapshots keyed OpenAI by model only; match until the next refresh.
      const legacy = "openai:" + model;
      if (cap.services[legacy]) return legacy;
    }
    return full;
  }
  function capacityServiceKey(req, sel) {
    if (!req) return null;
    if (req.kind === "model-tpm" && req.model) {
      const isEmbed = /embed/i.test(req.model) || /embed/i.test(req.service || "");
      let model = req.model;
      let version = req.modelVersion || "";
      let sku = req.sku || "Standard";
      if (sel) {
        const om = isEmbed ? sel.embeddingModel : (sel.chatModelName || sel.chatModel);
        const ov = isEmbed ? sel.embeddingModelVersion : sel.chatModelVersion;
        const os = isEmbed ? sel.embeddingModelSku : sel.chatModelSku;
        if (om && om !== model) {
          model = om;
          version = ov || openAiDefaultVersion(model, isEmbed);
          sku = os || openAiDefaultSku(model, version, isEmbed);
        } else {
          if (ov) version = ov;
          if (os) sku = os;
        }
      }
      return openAiKey(model, version, sku);
    }
    const p = (req.provider || "").toLowerCase();
    if (p.indexOf("microsoft.web/serverfarms") === 0) return "appservice";
    if (p.indexOf("microsoft.compute/virtualmachines") === 0) {
      // A chosen VM size checks its own family quota (vm:<size>); else regional.
      if (sel) {
        const vmKey = Object.keys(sel).find((k) => /vm.?size/i.test(k));
        const cap = state.capacity;
        if (vmKey && sel[vmKey] && cap && cap.services && cap.services["vm:" + sel[vmKey]]) {
          return "vm:" + sel[vmKey];
        }
      }
      return "vm";
    }
    if (p.indexOf("microsoft.app/managedenvironments") === 0) return "containerapps";
    if (p.indexOf("microsoft.avs/privateclouds") === 0) return "avs";
    if (p.indexOf("microsoft.fabric/capacities") === 0) return "fabric";
    if (p.indexOf("microsoft.powerbidedicated/capacities") === 0) return "powerbi";
    if (p.indexOf("microsoft.purview/accounts") === 0) return "purview";
    return null;
  }
  // Roll up every quota requirement of a use case to a single status for one
  // region, plus a per-service breakdown.
  function regionStatus(it, region, sel) {
    const cap = state.capacity;
    const reqs = (it.quota && it.quota.requirements) || [];
    const services = reqs.map((req) => {
      const key = capacityServiceKey(req, sel);
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
    const reg = state.registry;
    const capRegions = cap && cap.regions && cap.regions.length ? cap.regions : null;
    // The deployable region set is the registry's canonical list, so the picker
    // stays complete even when the capacity snapshot is stale or partial.
    // Capacity status overlays per region; regions absent from the snapshot show
    // "unknown" rather than disappearing from the dropdown.
    if (reg && Array.isArray(reg.regions) && reg.regions.length) {
      const extra = (capRegions || []).filter((r) => reg.regions.indexOf(r) === -1);
      return reg.regions.concat(extra);
    }
    return capRegions || ["centralus"];
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

  function costBlock(it, ctl) {
    const baseCost = it.cost;
    if (!baseCost) return null;

    const summaryTotalSpan = el("span", { class: "cost-summary-total" });
    const wrap = el("details", { class: "modal-section modal-card collapsible-card" }, [
      el("summary", { class: "collapse-summary" }, [
        el("span", { class: "cost-summary-lead" }, [
          el("span", { class: "collapse-title", role: "heading", "aria-level": "3", text: "Cost estimate" }),
          summaryTotalSpan,
        ]),
        el("span", { class: "collapse-caret", "aria-hidden": "true" }),
      ]),
    ]);
    const host = el("div", { class: "cost-body" });
    wrap.appendChild(host);

    function render(cost, opts) {
      host.innerHTML = "";
      const totalMonthly = typeof cost.totalMonthly === "number" ? cost.totalMonthly : null;
      const totalMoney = totalMonthly !== null ? fmtMoney(totalMonthly, cost.currency) : null;
      summaryTotalSpan.textContent =
        totalMoney && totalMonthly > 0
          ? `for the selected services: ~${totalMoney}/mo`
          : "for the selected services: usage-based";

      if (cost.profile) host.appendChild(el("p", { text: cost.profile }));

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

      host.appendChild(table);

      const meta = [];
      if (cost.region) meta.push(`Region ${cost.region}`);
      if (cost.pricedAt) meta.push(`retail prices as of ${cost.pricedAt}`);
      meta.push("PAYG list price, excludes tax/reservations/free tiers");
      if (opts && opts.forSelection) meta.push("estimated live for your current selections");
      if (cost.complete === false) meta.push("some components not priced — see notes");
      host.appendChild(el("p", { class: "cost-note", text: meta.join(" · ") }));
    } else if (components.length) {
      // No prices available — fall back to the component list.
      host.appendChild(
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

      host.appendChild(
        el("div", { class: "code", text: `./tools/Get-CatalogCost.ps1 -UseCase ${it.path}` })
      );
    }

    render(baseCost);

    if (ctl) {
      ctl.baseCost = baseCost;
      ctl.apply = function (est, opts) { host.classList.remove("cost-pending"); render(est, opts); };
      ctl.reset = function () { host.classList.remove("cost-pending"); render(baseCost); };
      ctl.pending = function () { host.classList.add("cost-pending"); };
    }

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
  function deployRegionField(it, getSel) {
    const sel = () => (getSel ? getSel() : null);
    const regions = capacityRegions();
    const select = el("select", { class: "deploy-input deploy-region", id: "deploy-region" });
    const options = {};
    let preferred = null;
    // Full label (with capacity) shows while the list is open; once a region is
    // picked the closed select shows just the region name.
    const fullText = {};
    let listOpen = false;
    regions.forEach((r) => {
      const rs = regionStatus(it, r, sel());
      fullText[r] = `${r} \u2014 ${ROLL_LABEL[rs.status]}`;
      const opt = el("option", { value: r, text: r });
      opt.setAttribute("data-status", rs.status);
      select.appendChild(opt);
      options[r] = opt;
      if (!preferred && rs.status === "green") preferred = r;
    });
    if (preferred) select.value = preferred;
    function applyLabels(open) {
      listOpen = open;
      regions.forEach((r) => { options[r].textContent = open ? fullText[r] : r; });
    }
    ["mousedown", "focus"].forEach((ev) => select.addEventListener(ev, () => applyLabels(true)));
    ["change", "blur"].forEach((ev) => select.addEventListener(ev, () => applyLabels(false)));
    // Escape closes the list without a change/blur, so reset labels too.
    select.addEventListener("keydown", (e) => { if (e.key === "Escape") applyLabels(false); });

    const note = el("div", { class: "deploy-region-note" });
    function refresh() {
      const r = select.value;
      const rs = regionStatus(it, r, sel());
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
    // Re-label every option and the note for the current advanced selections.
    function refreshAll() {
      regions.forEach((r) => {
        const rs = regionStatus(it, r, sel());
        fullText[r] = `${r} \u2014 ${ROLL_LABEL[rs.status]}`;
        options[r].textContent = listOpen ? fullText[r] : r;
        options[r].setAttribute("data-status", rs.status);
      });
      refresh();
    }
    return { select: select, note: note, refreshAll: refreshAll };
  }

  function deployBlock(it, estimateCtl) {
    const me = state.me;
    const maxHours = me && me.maxLifetimeHours ? me.maxLifetimeHours : 96;
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

    // Advanced-option overrides (param name -> value); shared with the region
    // picker so capacity reflects the chosen model/SKU.
    const advValues = {};
    const region = deployRegionField(it, function () { return advValues; });

    const endInput = el("input", {
      type: "datetime-local",
      class: "deploy-input",
      id: "deploy-end",
      value: localDatetimeValue(defaultEnd),
      min: localDatetimeValue(new Date(now.getTime() + 15 * 60 * 1000)),
    });
    // Admins may exceed the cap; everyone else is limited to maxHours.
    if (!me || !me.isAdmin) endInput.setAttribute("max", localDatetimeValue(maxEnd));

    const status = el("div", { class: "deploy-status", role: "status" });
    const btn = el("button", { class: "deploy-btn", type: "button", text: "Deploy to my subscription" });

    const maxLabel = maxHours % 24 === 0 ? `${maxHours / 24} day${maxHours / 24 === 1 ? "" : "s"}` : `${maxHours}h`;
    const lifeNote = me && me.isAdmin
      ? "As an admin you can set any end time. Resources are auto-deleted at the end time."
      : `Pick when this should be torn down (max ${maxLabel}). Resources are auto-deleted at the end time.`;

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
      const parameterValues = Object.assign({}, advValues);
      if (Object.keys(parameterValues).length) payload.parameterValues = parameterValues;
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

    // Advanced deployment options: dynamic controls generated from the template
    // parameters extracted into catalog.json (Build-Catalog.ps1). Selected values
    // override the template defaults on deploy and feed the manual CLI snippet.
    // Only scalar params are overridable; object/array/secret params are skipped.
    const params = (Array.isArray(it.parameters) ? it.parameters : []).filter(
      (p) => p.type === "string" || p.type === "int" || p.type === "bool"
    );
    const advOptsSummary = el("p", { class: "deploy-note deploy-advopts-summary" });
    const advOptsBtn = el("button", {
      class: "deploy-btn-secondary",
      type: "button",
      text: "Advanced deployment options",
    });
    advOptsBtn.addEventListener("click", openParamPanel);

    function advOverridePairs() {
      return Object.keys(advValues).map((k) => `${k}=${advValues[k]}`);
    }

    function updateAdvSummary() {
      const n = Object.keys(advValues).length;
      advOptsSummary.textContent = n
        ? `${n} option${n === 1 ? "" : "s"} customized. Values apply when you deploy.`
        : "Using recommended defaults for all template parameters.";
    }

    function paramDefaultString(p) {
      if (p.type === "bool") return String(p.default === true);
      return p.default === null || p.default === undefined ? "" : String(p.default);
    }

    // Registry-backed option sources (tools/Build-Registry.ps1). Models are
    // filtered to the OpenAI format the templates actually deploy; the registry
    // still records every provider for future provider-agnostic templates.
    const registry = state.registry || null;
    const CHAT_NAME_PARAMS = ["chatModelName", "chatModel"];
    const chatNameParam = params.map((p) => p.name).find((n) => CHAT_NAME_PARAMS.indexOf(n) !== -1);

    function regChatModels() {
      if (!registry || !registry.models) return [];
      return (registry.models.chat || []).filter((m) => m.provider === "OpenAI");
    }
    function regEmbedModels() {
      if (!registry || !registry.models) return [];
      return (registry.models.embedding || []).filter((m) => m.provider === "OpenAI");
    }
    function distinctNames(models) {
      const seen = [];
      models.forEach((m) => { if (seen.indexOf(m.name) === -1) seen.push(m.name); });
      return seen.sort();
    }
    function versionsFor(models, name) {
      return models.filter((m) => m.name === name).map((m) => m.version).filter((v) => v).sort();
    }
    function skusFor(models, name, version) {
      const entry = models.find((m) => m.name === name && m.version === version)
        || models.find((m) => m.name === name);
      return entry && Array.isArray(entry.skus) ? entry.skus.slice() : [];
    }

    // The registry SKU family for a non-model param, or null. Params that carry
    // a template @allowed set keep it (the compiled ARM template enforces it);
    // params without one source options from the registry so they become dropdowns
    // too. Matching is by convention (exact name, then the family name as a token,
    // then aliases) so a new use case's SKU/tier/size param maps automatically.
    function skuFamilyFor(p) {
      if (Array.isArray(p.allowed) && p.allowed.length) return null;
      if (!registry || !registry.skus) return null;
      if (registry.skus[p.name]) return p.name;
      const n = p.name.toLowerCase();
      const keys = Object.keys(registry.skus).sort((a, b) => b.length - a.length);
      for (let i = 0; i < keys.length; i++) {
        if (n.indexOf(keys[i].toLowerCase()) !== -1) return keys[i];
      }
      const aliases = [
        [/vm.?size|nodesize|hostsize/, "vmSize"],
        [/k8sversion|aksversion|kubernetes/, "kubernetesVersion"],
        [/runtimestack|linuxfx/, "linuxFxVersion"],
        [/sql.*tier/, "sqlTier"],
        [/sql.*sku/, "sqlSkuName"],
      ];
      for (let i = 0; i < aliases.length; i++) {
        if (aliases[i][0].test(n) && registry.skus[aliases[i][1]]) return aliases[i][1];
      }
      return null;
    }
    function regSkuValues(familyKey) {
      const fam = registry && registry.skus ? registry.skus[familyKey] : null;
      if (!fam || !Array.isArray(fam.values) || !fam.values.length) return null;
      const loc = region.select.value;
      const vals = fam.values
        .filter((v) => v.regions === "all" || !Array.isArray(v.regions) || !loc || v.regions.indexOf(loc) !== -1)
        .map((v) => String(v.value));
      return vals.length ? vals : null;
    }

    // The option list for a param, or null to fall back to @allowed/type input.
    function registryOptionsFor(p) {
      if (p.name === chatNameParam) {
        const n = distinctNames(regChatModels());
        return n.length ? n : null;
      }
      if (p.name === "embeddingModel") {
        const n = distinctNames(regEmbedModels());
        return n.length ? n : null;
      }
      if (p.name === "chatModelVersion") {
        const sel = advValues[chatNameParam] != null ? advValues[chatNameParam] : (chatNameParam ? paramDefaultOf(chatNameParam) : null);
        const v = versionsFor(regChatModels(), sel);
        return v.length ? v : null;
      }
      if (p.name === "chatModelSku") {
        const selName = advValues[chatNameParam] != null ? advValues[chatNameParam] : (chatNameParam ? paramDefaultOf(chatNameParam) : null);
        const selVer = advValues["chatModelVersion"] != null ? advValues["chatModelVersion"] : paramDefaultOf("chatModelVersion");
        const s = skusFor(regChatModels(), selName, selVer);
        return s.length ? s : null;
      }
      if (p.name === "embeddingModelVersion") {
        const sel = advValues["embeddingModel"] != null ? advValues["embeddingModel"] : paramDefaultOf("embeddingModel");
        const v = versionsFor(regEmbedModels(), sel);
        return v.length ? v : null;
      }
      const fam = skuFamilyFor(p);
      if (fam) return regSkuValues(fam);
      return null;
    }

    function paramDefaultOf(name) {
      const p = params.find((x) => x.name === name);
      return p ? paramDefaultString(p) : "";
    }

    function fillSelect(select, values, preferred) {
      select.innerHTML = "";
      values.forEach((v) => select.appendChild(el("option", { value: String(v), text: String(v) })));
      if (preferred != null && values.map(String).indexOf(String(preferred)) !== -1) select.value = String(preferred);
      else if (values.length) select.value = String(values[0]);
    }

    function openParamPanel() {
      if (!window.TechlabsPanel || !params.length) return;
      window.TechlabsPanel.open((bodyEl, e) => {
        bodyEl.appendChild(e("h2", { text: "Advanced deployment options" }));
        bodyEl.appendChild(
          e("p", {
            class: "deploy-note",
            text:
              `Customize the ${it.displayName} template parameters. Model and SKU choices ` +
              "come from the live Azure registry when available; otherwise the template's " +
              "own allowed values are used. Saved values apply when you deploy.",
          })
        );

        const controls = {};
        const formWrap = e("div", { class: "advopts-form" });
        // Render the SQL tier above its SKU (they cascade tier -> SKU).
        const orderedParams = params.slice();
        const ti = orderedParams.findIndex((p) => p.name === "sqlTier");
        const si = orderedParams.findIndex((p) => p.name === "sqlSkuName");
        if (ti !== -1 && si !== -1 && ti > si) {
          const tier = orderedParams.splice(ti, 1)[0];
          orderedParams.splice(orderedParams.findIndex((p) => p.name === "sqlSkuName"), 0, tier);
        }

        // Group each parameter under the Azure service it configures, so the
        // panel reads as a set of per-service boxes instead of one long list.
        // Name heuristics run first for consistent friendly labels; params they
        // don't classify fall back to the cost component's serviceName, else
        // "General".
        const costSvc = {};
        ((it.cost && it.cost.components) || []).forEach((c) => {
          ["skuParam", "instanceParam", "zoneRedundantParam", "skuQuantityParam", "skuPriceMultiplierParam"].forEach((k) => {
            if (c[k] && c.serviceName) costSvc[c[k]] = c.serviceName;
          });
        });
        // Normalize an Azure serviceName to the same friendly label the
        // heuristics produce, so both paths land in one box per service.
        function canonService(svc) {
          const s = String(svc).toLowerCase();
          if (/app service/.test(s)) return "App Service";
          if (/cognitive search|ai search/.test(s)) return "AI Search";
          if (/sql/.test(s)) return "Azure SQL";
          if (/openai|foundry/.test(s)) return "Azure OpenAI";
          if (/api management/.test(s)) return "API Management";
          if (/application gateway/.test(s)) return "Application Gateway";
          if (/container apps/.test(s)) return "Container Apps";
          if (/event hubs/.test(s)) return "Event Hubs";
          if (/stream analytics/.test(s)) return "Stream Analytics";
          if (/functions/.test(s)) return "Functions";
          if (/service bus/.test(s)) return "Service Bus";
          if (/cosmos/.test(s)) return "Cosmos DB";
          if (/virtual machines/.test(s)) return "Virtual machines";
          return svc;
        }
        function heuristicGroup(n) {
          if (/^(chat|embedding)/.test(n) || /model/.test(n)) return "Azure OpenAI";
          if (/sql/.test(n)) return "Azure SQL";
          if (/search/.test(n)) return "AI Search";
          if (/apim|publisher/.test(n)) return "API Management";
          if (/gateway|waf/.test(n)) return "Application Gateway";
          if (/session|hostpool|fslogix|scaling/.test(n)) return "Session hosts";
          if (/node|k8s|kubernetes|aks/.test(n)) return "Kubernetes";
          if (/replica/.test(n)) return "Container Apps";
          if (/servicebus/.test(n)) return "Service Bus";
          if (/cosmos/.test(n)) return "Cosmos DB";
          if (/eventhub|throughput|partition|streaming|messageretention/.test(n)) return "Streaming";
          if (/function/.test(n)) return "Functions";
          if (/databricks|lake/.test(n)) return "Data lake";
          if (/storage|blob/.test(n)) return "Storage";
          if (/firewall|bastion/.test(n)) return "Networking";
          if (/frontdoor/.test(n)) return "Front Door";
          if (/fabric/.test(n) || /admin/.test(n)) return "Fabric capacity";
          if (/app|zoneredundant|linuxfx|runtime|plan|capacity/.test(n)) return "App Service";
          return "General";
        }
        function groupFor(name) {
          const h = heuristicGroup(name.toLowerCase());
          if (h !== "General") return h;
          if (costSvc[name]) return canonService(costSvc[name]);
          return "General";
        }
        const groups = [];
        function bucket(gname) {
          let g = groups.find((x) => x.name === gname);
          if (!g) { g = { name: gname, fields: [] }; groups.push(g); }
          return g;
        }

        orderedParams.forEach((p) => {
          const fieldId = "advopt-" + p.name;
          const current = advValues[p.name];
          const defStr = paramDefaultString(p);
          const regOptions = registryOptionsFor(p);
          let control;
          let fromRegistry = false;
          if (regOptions) {
            fromRegistry = true;
            control = e("select", { class: "deploy-input", id: fieldId },
              regOptions.map((a) => e("option", { value: String(a), text: String(a) })));
            control.value = current != null && regOptions.map(String).indexOf(String(current)) !== -1
              ? current
              : (regOptions.map(String).indexOf(defStr) !== -1 ? defStr : String(regOptions[0]));
          } else if (p.type === "bool") {
            control = e("select", { class: "deploy-input", id: fieldId }, [
              e("option", { value: "true", text: "true" }),
              e("option", { value: "false", text: "false" }),
            ]);
            control.value = current != null ? current : defStr || "false";
          } else if (Array.isArray(p.allowed) && p.allowed.length) {
            control = e(
              "select",
              { class: "deploy-input", id: fieldId },
              p.allowed.map((a) => e("option", { value: String(a), text: String(a) }))
            );
            control.value = current != null ? current : defStr || String(p.allowed[0]);
          } else if (p.type === "int") {
            const props = {
              type: "number",
              class: "deploy-input",
              id: fieldId,
              value: current != null ? current : defStr,
            };
            if (p.min !== null && p.min !== undefined) props.min = p.min;
            if (p.max !== null && p.max !== undefined) props.max = p.max;
            control = e("input", props);
          } else {
            control = e("input", {
              type: "text",
              class: "deploy-input",
              id: fieldId,
              value: current != null ? current : defStr,
            });
          }

          const meta = [];
          if (p.description) meta.push(e("span", { class: "advopt-desc", text: p.description }));
          const hintBits = [];
          if (fromRegistry) hintBits.push("live from Azure registry");
          if (p.type === "int" && (p.min != null || p.max != null)) {
            hintBits.push(`range ${p.min != null ? p.min : "-"}..${p.max != null ? p.max : "-"}`);
          }
          if (defStr !== "") hintBits.push(`default ${defStr}`);
          if (hintBits.length) meta.push(e("span", { class: "advopt-hint", text: hintBits.join(" \u00b7 ") }));

          const field = e(
            "div",
            { class: "advopt-field" },
            [e("label", { class: "deploy-label", for: fieldId, text: p.name }), control].concat(meta)
          );
          bucket(groupFor(p.name)).fields.push(field);
          controls[p.name] = { control: control, param: p };
        });

        // "General" reads as a catch-all, so render it last.
        groups.sort((a, b) => (a.name === "General" ? 1 : 0) - (b.name === "General" ? 1 : 0));
        groups.forEach((g) => {
          formWrap.appendChild(
            e("fieldset", { class: "advopt-group" }, [
              e("legend", { class: "advopt-legend", text: g.name }),
              e("div", { class: "advopt-grid" }, g.fields),
            ])
          );
        });
        bodyEl.appendChild(formWrap);

        // Cascade: when a model name changes, refresh its version and SKU lists.
        const chatCtl = chatNameParam && controls[chatNameParam];
        const chatVerCtl = controls["chatModelVersion"];
        const chatSkuCtl = controls["chatModelSku"];
        function refreshChat() {
          if (!chatCtl) return;
          const name = chatCtl.control.value;
          if (chatVerCtl && chatVerCtl.control.tagName === "SELECT") {
            const vers = versionsFor(regChatModels(), name);
            if (vers.length) fillSelect(chatVerCtl.control, vers, paramDefaultOf("chatModelVersion"));
          }
          if (chatSkuCtl && chatSkuCtl.control.tagName === "SELECT") {
            const ver = chatVerCtl ? chatVerCtl.control.value : paramDefaultOf("chatModelVersion");
            const sk = skusFor(regChatModels(), name, ver);
            if (sk.length) fillSelect(chatSkuCtl.control, sk, paramDefaultOf("chatModelSku"));
          }
        }
        if (chatCtl && chatCtl.control.tagName === "SELECT" && regChatModels().length) {
          chatCtl.control.addEventListener("change", refreshChat);
          if (chatVerCtl && chatVerCtl.control.tagName === "SELECT") {
            chatVerCtl.control.addEventListener("change", () => {
              if (chatSkuCtl && chatSkuCtl.control.tagName === "SELECT") {
                const sk = skusFor(regChatModels(), chatCtl.control.value, chatVerCtl.control.value);
                if (sk.length) fillSelect(chatSkuCtl.control, sk, paramDefaultOf("chatModelSku"));
              }
            });
          }
        }
        const embedCtl = controls["embeddingModel"];
        const embedVerCtl = controls["embeddingModelVersion"];
        if (embedCtl && embedCtl.control.tagName === "SELECT" && regEmbedModels().length && embedVerCtl) {
          embedCtl.control.addEventListener("change", () => {
            const vers = versionsFor(regEmbedModels(), embedCtl.control.value);
            if (vers.length && embedVerCtl.control.tagName === "SELECT") {
              fillSelect(embedVerCtl.control, vers, paramDefaultOf("embeddingModelVersion"));
            }
          });
        }

        // Azure SQL tier and SKU are coupled (GeneralPurpose->GP_*, etc.): filter
        // the SKU list to the selected tier so mismatched pairs can't be chosen.
        const sqlTierCtl = controls["sqlTier"];
        const sqlSkuCtl = controls["sqlSkuName"];
        if (sqlTierCtl && sqlSkuCtl && sqlSkuCtl.control.tagName === "SELECT") {
          const tierPrefix = (t) => (t === "BusinessCritical" ? "BC_" : t === "Hyperscale" ? "HS_" : "GP_");
          const skusForTier = (t) => {
            const all = regSkuValues("sqlSkuName") || [];
            const f = all.filter((s) => s.indexOf(tierPrefix(t)) === 0);
            return f.length ? f : all;
          };
          fillSelect(sqlSkuCtl.control, skusForTier(sqlTierCtl.control.value), advValues["sqlSkuName"] != null ? advValues["sqlSkuName"] : paramDefaultOf("sqlSkuName"));
          if (sqlTierCtl.control.tagName === "SELECT") {
            sqlTierCtl.control.addEventListener("change", () => {
              fillSelect(sqlSkuCtl.control, skusForTier(sqlTierCtl.control.value), paramDefaultOf("sqlSkuName"));
            });
          }
        }

        // App Service zone redundancy needs a Premium v3 plan and >= 3 instances;
        // warn live when the toggle is on but the coupled SKU/capacity don't qualify.
        const zrParam = ["appZoneRedundant", "zoneRedundant"].find((n) => controls[n]);
        const zrSkuParam = ["appServiceSku", "sku"].find((n) => controls[n]);
        const zrCapParam = ["appCapacity", "appServiceCapacity", "capacity"].find((n) => controls[n]);
        if (zrParam && (zrSkuParam || zrCapParam)) {
          const warn = e("span", { class: "advopt-warn" });
          const zrField = controls[zrParam].control.closest(".advopt-field");
          if (zrField) zrField.appendChild(warn);
          const isPremiumV3 = (v) => /^P\w*v3$/i.test(v);
          const checkZr = () => {
            const needs = [];
            if (controls[zrParam].control.value === "true") {
              if (zrSkuParam && !isPremiumV3(controls[zrSkuParam].control.value)) needs.push("a Premium v3 plan (P1v3+)");
              if (zrCapParam && Number(controls[zrCapParam].control.value) < 3) needs.push("at least 3 instances");
            }
            if (needs.length) {
              const fields = [zrSkuParam, zrCapParam].filter(Boolean).join(" / ");
              warn.textContent = `Zone redundancy needs ${needs.join(" and ")} — update ${fields} above.`;
              warn.style.display = "";
            } else {
              warn.textContent = "";
              warn.style.display = "none";
            }
          };
          controls[zrParam].control.addEventListener("change", checkZr);
          if (zrSkuParam) controls[zrSkuParam].control.addEventListener("change", checkZr);
          if (zrCapParam) {
            controls[zrCapParam].control.addEventListener("change", checkZr);
            controls[zrCapParam].control.addEventListener("input", checkZr);
          }
          checkZr();
        }

        const saveBtn = e("button", { class: "deploy-btn", type: "button", text: "Save options" });
        const cancelBtn = e("button", { class: "deploy-btn-secondary", type: "button", text: "Cancel" });
        saveBtn.addEventListener("click", () => {
          Object.keys(controls).forEach((name) => {
            const entry = controls[name];
            const val = entry.control.value;
            const defStr = paramDefaultString(entry.param);
            if (val === null || val === undefined || val === "" || val === defStr) {
              delete advValues[name];
            } else {
              advValues[name] = val;
            }
          });
          updateAdvSummary();
          syncEngine();
          scheduleEstimate();
          if (region.refreshAll) region.refreshAll();
          window.TechlabsPanel.close();
        });
        cancelBtn.addEventListener("click", () => window.TechlabsPanel.close());
        bodyEl.appendChild(e("div", { class: "advopts-actions" }, [saveBtn, cancelBtn]));
      });
    }

    updateAdvSummary();

    // Live cost re-estimate: when the region or an advanced option changes, ask
    // the backend to re-price for the selection; fall back to the static cost.
    let estimateTimer = null;
    let estimateSeq = 0;
    function scheduleEstimate() {
      if (!estimateCtl || !estimateCtl.apply || !window.TechlabsApi) return;
      const baseRegion = (estimateCtl.baseCost && estimateCtl.baseCost.region) || "";
      const loc = region.select.value;
      const values = Object.assign({}, advValues);
      const changed = Object.keys(values).length > 0 || loc.toLowerCase() !== String(baseRegion).toLowerCase();
      if (!changed) {
        estimateCtl.reset();
        return;
      }
      estimateCtl.pending();
      clearTimeout(estimateTimer);
      estimateTimer = setTimeout(function () {
        const seq = ++estimateSeq;
        window.TechlabsApi.estimateCost({ useCase: it.path, location: loc, parameterValues: values })
          .then(function (est) { if (seq === estimateSeq) estimateCtl.apply(est, { forSelection: true }); })
          .catch(function () { if (seq === estimateSeq) estimateCtl.reset(); });
      }, 350);
    }

    const deployField = (labelText, control, extra, cls) =>
      el("div", { class: "deploy-field" + (cls ? " " + cls : "") }, [
        el("label", { class: "deploy-label", for: control.id, text: labelText }),
        control,
        extra || null,
      ]);

    region.select.setAttribute("aria-label", "Region");
    const regionBox = el("fieldset", { class: "deploy-group deploy-region-box" }, [
      el("legend", { class: "deploy-legend", text: "Region" }),
      el("div", { class: "deploy-field" }, [region.select, region.note]),
    ]);
    const envBox = el("fieldset", { class: "deploy-group" }, [
      el("legend", { class: "deploy-legend", text: "Environment" }),
      el("div", { class: "deploy-grid" }, [
        deployField("Type", envSelect),
        deployField("IaC engine", engineSelect, engineHint),
        deployField("Resource group", rgInput),
        deployField("End time (auto-cleanup)", endInput, el("p", { class: "deploy-note", text: lifeNote })),
      ]),
    ]);
    const topRow = el("div", { class: "deploy-toprow" }, [regionBox, envBox]);

    // Advanced options sit below the deploy button; the CLI snippet comes last.
    const advSection = el(
      "div",
      { class: "deploy-advanced-section" },
      params.length ? [el("div", { class: "deploy-advopts" }, [advOptsBtn, advOptsSummary])] : []
    );

    function manualSnippet(engine) {
      const env = envSelect.value;
      const location = region.select.value;
      const rg = (rgInput.value || "rg-techlabs").trim();
      const overrides = advOverridePairs();

      if (engine === "terraform") {
        const tfArgs = [
          `-var-file=${env}.tfvars`,
          `-var "resource_group_name=${rg}"`,
          `-var "location=${location}"`,
        ];
        overrides.forEach((line) => {
          const i = line.indexOf("=");
          if (i < 1) return;
          const key = line.slice(0, i).trim();
          const value = line.slice(i + 1).trim();
          if (!key || !value) return;
          // Bicep params are camelCase; Terraform variables are the snake_case form.
          const tfKey = key.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
          tfArgs.push(`-var "${tfKey}=${value}"`);
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
    [engineSelect, envSelect, region.select, rgInput].forEach((control) => {
      control.addEventListener("change", syncEngine);
      control.addEventListener("input", syncEngine);
    });
    region.select.addEventListener("change", scheduleEstimate);
    syncEngine();
    const manual = el("details", { class: "deploy-manual" }, [manualSummary, manualCode]);

    const footer = el("div", { class: "deploy-foot" }, [btn, status]);
    // Advanced options under Region (left); deploy button under Environment (right).
    topRow.appendChild(advSection);
    topRow.appendChild(footer);

    const card = el("div", { class: "modal-section modal-card deploy-card is-open" });
    const header = el("div", { class: "deploy-head deploy-head-static" }, [el("h3", { text: "Deploy" })]);
    [header, topRow, manual].forEach((n) => card.appendChild(n));
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
    const estimateCtl = {};
    body.appendChild(deployBlock(it, estimateCtl));

    // Cost estimate (boxed).
    const cost = costBlock(it, estimateCtl);
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

  // Live model + service SKU registry (tools/Build-Registry.ps1, refreshed every
  // few days). Best-effort: advanced deployment options fall back to the
  // template's own @allowed sets when it is missing.
  fetch("registry.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((reg) => {
      state.registry = reg;
    })
    .catch(() => {
      /* no registry — advanced options use template defaults */
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
