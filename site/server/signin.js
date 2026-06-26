(function () {
  var btn = document.getElementById("signin");
  var errBox = document.getElementById("error");

  // Surface any provider error passed back as ?message=...&error=...
  var params = new URLSearchParams(window.location.search);
  var msg = params.get("message") || params.get("error_description");
  if (params.get("error") || msg) {
    errBox.textContent = msg || params.get("error");
    errBox.hidden = false;
  }

  function showError(text) {
    errBox.textContent = text || "network error";
    errBox.hidden = false;
    btn.disabled = false;
    btn.textContent = "Sign in with Microsoft →";
  }

  // Auth-code + PKCE loopback flow (same as the BusinessAgent web app):
  // ask the server for an authorize URL, then hand the browser to Microsoft.
  // Entra redirects back to http://localhost:<port>/?code=... which the
  // server finalizes before serving the gallery.
  btn.addEventListener("click", function () {
    btn.disabled = true;
    btn.textContent = "Redirecting to Microsoft…";
    errBox.hidden = true;
    fetch("/api/auth/entra/start", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("start failed: HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data.auth_url) throw new Error("no authorize URL returned");
        window.location.assign(data.auth_url);
      })
      .catch(function (e) {
        showError(e && e.message ? e.message : "network error");
      });
  });
})();
