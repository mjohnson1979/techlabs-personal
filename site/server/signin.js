(function () {
  var btn = document.getElementById("signin");
  var errBox = document.getElementById("error");

  // Surface any provider error passed back as ?error=...&message=...
  var params = new URLSearchParams(window.location.search);
  var msg = params.get("message") || params.get("error_description");
  if (params.get("error") || msg) {
    errBox.textContent = msg || params.get("error");
    errBox.hidden = false;
  }

  btn.addEventListener("click", function () {
    btn.disabled = true;
    btn.textContent = "Redirecting to Microsoft…";
    fetch("/api/auth/entra/start", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("start failed: HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data.auth_url) throw new Error("no auth_url returned");
        // Top-level navigation in the real browser carries the
        // Windows SSO / device-compliance claims Entra requires.
        window.location.assign(data.auth_url);
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = "Sign in with Microsoft →";
        errBox.textContent = e && e.message ? e.message : "network error";
        errBox.hidden = false;
      });
  });
})();
