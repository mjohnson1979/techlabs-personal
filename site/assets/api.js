/* Techlabs gallery API client. Same-origin fetch helpers shared by the
   catalog, account, and admin views. Exposed as window.TechlabsApi. */
(function () {
  "use strict";

  async function request(method, url, body) {
    const opts = {
      method: method,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    if (resp.status === 204) return null;
    let data = null;
    const text = await resp.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = { detail: text };
      }
    }
    if (!resp.ok) {
      const msg = (data && (data.detail || data.message)) || `HTTP ${resp.status}`;
      const err = new Error(msg);
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  window.TechlabsApi = {
    me: () => request("GET", "/api/me"),

    recordEvent: (type, useCase) =>
      request("POST", "/api/events", { type: type, useCase: useCase || "" }).catch(
        () => null /* telemetry is best-effort; never break the UI */
      ),

    createDeployment: (payload) =>
      request("POST", "/api/deployments", payload).then((d) => d.deployment),

    getDeployment: (id) =>
      request("GET", "/api/deployments/" + encodeURIComponent(id)).then((d) => d.deployment),

    listDeployments: (scope) =>
      request("GET", "/api/deployments?scope=" + encodeURIComponent(scope || "mine")).then(
        (d) => d.deployments
      ),

    destroyDeployment: (id) =>
      request("POST", "/api/deployments/" + encodeURIComponent(id) + "/destroy").then(
        (d) => d.deployment
      ),

    adminTelemetry: (days) =>
      request("GET", "/api/admin/telemetry?days=" + encodeURIComponent(days || 30)),

    adminUsers: () => request("GET", "/api/admin/users").then((d) => d.users),

    setUserRole: (upn, role) =>
      request("POST", "/api/admin/users", { upn: upn, role: role }),

    // Multipart upload of a use-case template zip. Lets the browser set the
    // multipart boundary, so we don't reuse the JSON `request` helper.
    uploadUseCase: async (file, displayName) => {
      const form = new FormData();
      form.append("file", file);
      form.append("displayName", displayName || "");
      const resp = await fetch("/api/use-cases/upload", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        body: form,
      });
      let data = null;
      const text = await resp.text();
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (e) {
          data = { detail: text };
        }
      }
      if (!resp.ok) {
        const msg = (data && (data.detail || data.message)) || `HTTP ${resp.status}`;
        const err = new Error(msg);
        err.status = resp.status;
        throw err;
      }
      return data;
    },

    adminSubmissions: () =>
      request("GET", "/api/admin/submissions").then((d) => d.submissions),

    submissionDownloadUrl: (id) =>
      "/api/admin/submissions/" + encodeURIComponent(id) + "/download",

    discardSubmission: (id) =>
      request("POST", "/api/admin/submissions/" + encodeURIComponent(id) + "/discard"),
  };
})();
