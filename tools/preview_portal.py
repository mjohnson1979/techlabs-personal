"""Serve an exported organization UI locally with read-only synthetic API responses."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC_FILES = (
    "index.html", "catalog.json", "capacity.json", "updates.json", "registry.json",
    "assets/account.js", "assets/admin.js", "assets/api.js", "assets/app.js",
    "assets/panel.js", "assets/styles.css", "assets/user-menu.js", "assets/setup.js",
    "install.html", "assets/install.js", "assets/install.css",
)
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


def create_preview_server(root: Path, port: int = 8766) -> ThreadingHTTPServer:
    site = (root / "site").resolve()
    marker = site / "distribution.json"
    if not marker.is_file():
        raise ValueError("Use an extracted organization export with site/distribution.json")
    data = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("distribution") not in ("organization", "customer"):
        raise ValueError("The distribution marker must identify an organization export")

    files: dict[str, tuple[str, bytes]] = {}
    for relative in STATIC_FILES:
        source = site / relative
        if source.is_symlink() or source.parent.is_symlink():
            raise ValueError("Preview assets must not be symbolic links")
        source.resolve().relative_to(site)
        if source.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("Preview assets must not exceed 5 MiB per file")
        files["/" + relative] = (CONTENT_TYPES[source.suffix], source.read_bytes())
    files["/"] = files["/index.html"]
    files["/install"] = files["/install.html"]

    user = {"oid": "preview-operator", "upn": "preview@example.com", "name": "Preview Operator"}
    api = {
        "/api/install/status": {"detail": "Read-only installation preview"},
        "/healthz": {"status": "ok", "mode": "read-only-preview"},
        "/api/auth/entra/whoami": {**user, "signed_in": True, "tenant_id": "preview-only"},
        "/api/me": {
            **user, "signedIn": True, "role": "admin", "isAdmin": True,
            "brandName": "Deployment Portal (Preview)", "customerOperated": True,
            "footerText": "Read-only preview. Cost estimates are indicative.",
            "deploymentCount": 0, "activeDeployments": 0, "maxLifetimeHours": 48,
            "terraformExecAvailable": False,
            "setupRequired": True,
            "subscriptions": [],
        },
        "/api/deployments": {"deployments": []},
        "/api/admin/setup": {
            "branding": {"brandName": "Deployment Portal (Preview)", "footerText": "Read-only preview. Cost estimates are indicative."},
            "azureTenantId": "11111111-1111-4111-8111-111111111111",
            "primaryAuthentication": {"clientId": "22222222-2222-4222-8222-222222222222", "verified": False},
            "subscriptions": [], "authentication": {}, "github": {"enabled": False},
            "githubCredentialConfigured": False,
            "githubDeviceConfigured": True, "githubConnected": False,
            "githubCredentialStorage": {"secretStorage": "app_service"},
        },
        "/api/admin/submissions": {"submissions": []},
        "/api/admin/users": {"users": [{**user, "role": "admin", "createdAt": None, "lastSeen": None}]},
        "/api/admin/telemetry": {
            "days": 30, "byType": {}, "topViews": [], "topDeploys": [],
            "topUsers": [], "statusCounts": {}, "perDay": [],
            "activeDeployments": 0, "uniqueUsers": 0,
        },
    }
    for route, body in api.items():
        files[route] = ("application/json", json.dumps(body).encode("utf-8"))

    class PreviewHandler(BaseHTTPRequestHandler):
        def _respond(self, *, head_only: bool = False) -> None:
            allowed_hosts = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            status = 200
            content_type = "application/json"
            if self.headers.get("Host", "").lower() not in allowed_hosts:
                status, content = 403, b'{"detail":"invalid preview host"}'
            elif self.command not in ("GET", "HEAD"):
                status = 405
                content = b'{"detail":"Read-only preview: changes, uploads and deployments are disabled."}'
            else:
                try:
                    path = urlsplit(self.path).path
                except ValueError:
                    path = ""
                entry = files.get(path)
                if entry is None:
                    status, content = 404, b'{"detail":"not found"}'
                else:
                    content_type, content = entry
                    if path == "/api/install/status":
                        status = 401
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Preview-Mode", "read-only")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            if status == 405:
                self.send_header("Allow", "GET, HEAD")
            self.end_headers()
            if not head_only:
                self.wfile.write(content)

        def do_GET(self) -> None:
            self._respond()

        def do_HEAD(self) -> None:
            self._respond(head_only=True)

        def do_POST(self) -> None:
            self._respond()

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

    return ThreadingHTTPServer(("127.0.0.1", port), PreviewHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        server = create_preview_server(args.root, args.port)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Preview cannot start: {exc}\n")
    with server:
        sys.stdout.write(
            f"Read-only preview: http://127.0.0.1:{server.server_port}/\n"
            "Synthetic identity only. No production backend, credentials, uploads or deployments.\n"
            "Do not expose or tunnel this server. Press Ctrl+C to stop.\n"
        )
        sys.stdout.flush()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()