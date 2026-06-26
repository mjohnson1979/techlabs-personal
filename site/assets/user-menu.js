// Signed-in user badge in the header: avatar + name, with a dropdown to sign
// out. Identity comes from /api/auth/entra/whoami; the avatar is the Microsoft
// profile photo from /api/auth/entra/photo (initials fallback when absent).
(function () {
  "use strict";

  var menu = document.getElementById("user-menu");
  var button = document.getElementById("user-button");
  var dropdown = document.getElementById("user-dropdown");
  var nameEl = document.getElementById("user-name");
  var avatarEl = document.getElementById("user-avatar");
  var avatarLgEl = document.getElementById("user-avatar-lg");
  var cardName = document.getElementById("user-card-name");
  var cardUpn = document.getElementById("user-card-upn");
  var signoutBtn = document.getElementById("signout-button");

  if (!menu || !button) return;

  var logoutUrl = "";

  function initials(name, upn) {
    var src = (name || upn || "?").trim();
    var parts = src.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
      return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
    }
    return src.charAt(0).toUpperCase();
  }

  function paintInitials(el, text) {
    el.textContent = text;
    el.classList.add("is-initials");
    el.style.backgroundImage = "";
  }

  function paintPhoto(el, url) {
    el.textContent = "";
    el.classList.remove("is-initials");
    el.style.backgroundImage = 'url("' + url + '")';
  }

  function setAvatar(text) {
    paintInitials(avatarEl, text);
    paintInitials(avatarLgEl, text);
    // Try the Microsoft profile photo; keep initials if there isn't one.
    var img = new Image();
    img.onload = function () {
      paintPhoto(avatarEl, "/api/auth/entra/photo");
      paintPhoto(avatarLgEl, "/api/auth/entra/photo");
    };
    img.src = "/api/auth/entra/photo";
  }

  function openMenu() {
    dropdown.hidden = false;
    button.setAttribute("aria-expanded", "true");
    document.addEventListener("click", onDocClick, true);
    document.addEventListener("keydown", onKeydown, true);
  }

  function closeMenu() {
    dropdown.hidden = true;
    button.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", onDocClick, true);
    document.removeEventListener("keydown", onKeydown, true);
  }

  function onDocClick(e) {
    if (!menu.contains(e.target)) closeMenu();
  }

  function onKeydown(e) {
    if (e.key === "Escape") {
      closeMenu();
      button.focus();
    }
  }

  button.addEventListener("click", function () {
    if (dropdown.hidden) openMenu();
    else closeMenu();
  });

  signoutBtn.addEventListener("click", function () {
    signoutBtn.disabled = true;
    signoutBtn.textContent = "Signing out…";
    // Behind App Service Easy Auth, sign-out must hit the platform logout
    // endpoint to clear the platform session; otherwise clear the app session.
    if (logoutUrl) {
      window.location.assign(logoutUrl);
      return;
    }
    fetch("/api/auth/entra/signout", {
      method: "POST",
      credentials: "same-origin",
    })
      .catch(function () {})
      .then(function () {
        window.location.assign("/");
      });
  });

  fetch("/api/auth/entra/whoami", { credentials: "same-origin" })
    .then(function (r) {
      return r.ok ? r.json() : null;
    })
    .then(function (data) {
      if (!data || !data.signed_in) return;
      logoutUrl = data.logout_url || "";
      var display = data.name || data.upn;
      nameEl.textContent = display;
      cardName.textContent = display;
      cardUpn.textContent = data.upn;
      setAvatar(initials(data.name, data.upn));
      menu.hidden = false;
    })
    .catch(function () {});
})();
