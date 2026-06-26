/* SME&C Techlabs gallery - loads catalog.json and renders filterable cards. */
(function () {
  "use strict";

  const REPO = "https://github.com/sebaclem/azure-tech-labs";
  const state = { items: [], q: "", category: new Set(), industry: new Set(), compliance: new Set() };

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
      el("div", { class: "chips" }, chips),
    ]);
    node.addEventListener("click", () => openModal(it));
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter") openModal(it);
    });
    return node;
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

  function openModal(it) {
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

    body.appendChild(
      el("div", { class: "modal-section" }, [
        el("h3", { text: "Deploy" }),
        el("div", {
          class: "code",
          text: `az group create --name rg-techlabs --location eastus\naz deployment group create \`\n  --resource-group rg-techlabs \`\n  --template-file ${it.template} \`\n  --parameters catalog/${it.path}/parameters/dev.bicepparam`,
        }),
      ])
    );

    body.appendChild(
      el("div", { class: "modal-section" }, [
        el("h3", { text: "Estimate cost" }),
        el("div", { class: "code", text: `./tools/Get-CatalogCost.ps1 -UseCase ${it.path}` }),
      ])
    );

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
