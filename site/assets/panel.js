/* Shared DOM helper + full-screen panel overlay used by the account and admin
   views. Exposes window.TechlabsDom.el and window.TechlabsPanel. */
(function () {
  "use strict";

  function el(tag, props, children) {
    const node = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach((k) => {
      const v = props[k];
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
    });
    const list = Array.isArray(children) ? children : children ? [children] : [];
    list.forEach((c) => {
      if (c === null || c === undefined) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  const panel = document.getElementById("panel");
  const body = document.getElementById("panel-body");

  function close() {
    if (panel) panel.hidden = true;
    if (body) body.innerHTML = "";
  }

  function open(buildFn) {
    if (!panel || !body) return;
    body.innerHTML = "";
    buildFn(body, el);
    panel.hidden = false;
  }

  if (panel) {
    panel.querySelectorAll("[data-panel-close]").forEach((n) =>
      n.addEventListener("click", close)
    );
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && panel && !panel.hidden) close();
    });
  }

  window.TechlabsDom = { el: el };
  window.TechlabsPanel = { open: open, close: close };
})();
