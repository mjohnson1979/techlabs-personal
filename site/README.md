# SME&C Techlabs — gallery site

A self-contained static site that renders the use case catalog as filterable cards,
modeled on the Trainer-Demo-Deploy gallery. No build tooling or framework required.

## Files

- `index.html` — page shell (header, hero, filters, cards, modal).
- `assets/styles.css` — styling.
- `assets/app.js` — loads `catalog.json`, renders cards, search + filters, detail modal.
- `catalog.json` — generated from every `catalog/**/metadata.json` by `tools/Build-Catalog.ps1`.

## Regenerate the catalog

Run after adding or editing a use case's `metadata.json`:

```powershell
./tools/Build-Catalog.ps1
```

## Preview locally

`catalog.json` is loaded via `fetch`, so open the site through a local web server
(not `file://`):

```powershell
cd site
python -m http.server 8080
# then browse to http://localhost:8080
```

## Internal access — Microsoft Entra ID sign-in

The gallery is **internal-only**. A small auth server ([`server/`](server/)) gates the
static site behind **Microsoft Entra ID** using a server-side OAuth **authorization-code +
PKCE** flow — the same pattern as our BusinessAgent web app. Unauthenticated requests are
redirected to Microsoft sign-in; only users from your tenant can reach the gallery.

Identity only — **no app registration, no client secret, no Key Vault, no token storage**.

Security controls (see [`server/entra_auth.py`](server/entra_auth.py) and
[`server/app.py`](server/app.py)):

- **Entra ID auth-code + PKCE** — reuses the Azure CLI public client
  (`04b07795-…`), which every tenant user has already consented to, so there is nothing to
  register. PKCE (S256) + a random `state` protect the exchange.
- **Tenant restriction** — set `GALLERY_ENTRA_TENANT_ID` to your tenant GUID so only your
  organization's accounts can sign in.
- **Opaque session** — an `HttpOnly`, `SameSite=Lax` cookie indexes an in-memory session;
  no tokens are persisted. Restarting the server forces re-sign-in.
- **Security headers** — Content-Security-Policy (`default-src 'self'`),
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, strict Referrer-Policy,
  locked-down Permissions-Policy, COOP/CORP `same-origin`, `Cache-Control: no-store`, and
  HSTS when served over HTTPS.

### Run it

```powershell
cd site/server
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Restrict sign-in to your tenant (see .env.example)
$env:GALLERY_ENTRA_TENANT_ID = "<YOUR_TENANT_GUID>"

uvicorn app:app --host 127.0.0.1 --port 8000
```

Browse **http://localhost:8000/** → you're redirected to Microsoft to sign in → after
sign-in the gallery loads. Sign out via `POST /api/auth/entra/signout`.

> **HTTPS in production:** run behind a TLS-terminating reverse proxy (e.g. Caddy, nginx,
> or Azure Application Gateway) and set `GALLERY_HTTPS=true` to enable `Secure` cookies and
> HSTS. The Azure CLI public client only allows `http://localhost[:port]/` redirect URIs,
> so a non-localhost hostname requires registering your own Entra app and pointing
> `redirect_uri` at it — swap the `client_id` in [`server/entra_auth.py`](server/entra_auth.py).

_Maintenance note: this README received a docs-only update to trigger the deployment workflow._

### Endpoints

| Route | Purpose |
| --- | --- |
| `GET /api/auth/entra/start` | Begin sign-in; returns `{ auth_url }` (or `?redirect=1` to 302). |
| `GET /` (with `?code&state`) | OAuth landing; exchanges the code and establishes the session. |
| `GET /api/auth/entra/whoami` | Current sign-in state (UPN, name, oid, tenant). |
| `POST /api/auth/entra/signout` | Clear the session. |

> The static-only [GitHub Pages workflow](../.github/workflows/pages.yml) publishes the
> gallery **without** authentication — leave GitHub Pages disabled (Settings → Pages →
> Source: None) for an internal site, and serve it through the auth server above instead.


