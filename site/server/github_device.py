"""Administrator-bound GitHub device authorization; credentials remain server-side."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from azure.core.exceptions import AzureError

_SCOPES = "repo read:org workflow"
_DEVICE_START_ERRORS = {
    "device_flow_disabled": (
        "GitHub Device Flow is disabled. Open the saved OAuth App's settings, "
        "enable Device Flow, save, then select Connect GitHub again."
    ),
    "incorrect_client_credentials": (
        "GitHub rejected the OAuth Client ID. In Setup, save the Client ID from "
        "your organization-owned GitHub OAuth App, not its name or client secret, "
        "and enable Device Flow."
    ),
    "invalid_client": (
        "GitHub rejected the OAuth Client ID. Verify that the saved Client ID "
        "belongs to your organization-owned GitHub OAuth App and that Device Flow is enabled."
    ),
}


@dataclass
class DeviceFlow:
    owner: str
    client_id: str
    device_code: str = field(repr=False)
    expires_at: float
    interval: int
    next_poll: float
    token: str = field(default="", repr=False)
    token_expires_at: float = 0
    refresh_token: str = field(default="", repr=False)
    refresh_expires_at: float = 0
    storage: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_flows: dict[str, DeviceFlow] = {}
_renewal_lock = asyncio.Lock()
_pending_renewal: dict | None = None
_renewal_failed_source = ""
_renewal_retry_after = 0.0


def _credentials() -> dict:
    value = os.environ.get("GALLERY_GITHUB_TOKEN", "").strip()
    if not value or value.startswith("@Microsoft.KeyVault("):
        return {}
    if value.startswith("{"):
        try:
            document = json.loads(value)
        except ValueError:
            return {}
        if not isinstance(document, dict) or document.get("version") != 1:
            return {}
        return document
    return {"accessToken": value, "expiresAt": os.environ.get("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "0") or "0"}


def _deadline(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return -1
    return number if math.isfinite(number) and number >= 0 and not isinstance(value, bool) else -1


def client_id() -> str:
    import db

    value = str(db.get_setup_setting("github_oauth_client", "") or os.environ.get("GALLERY_GITHUB_OAUTH_CLIENT_ID", "")).strip()
    return value if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else ""


def access_token() -> str:
    source = os.environ.get("GALLERY_GITHUB_TOKEN", "").strip()
    if (_pending_renewal and _pending_renewal["source"] == source) or (source and source == _renewal_failed_source):
        return ""
    credentials = _credentials()
    value = credentials.get("accessToken")
    if not isinstance(value, str) or not value or len(value) > 4096:
        return ""
    expiry = _deadline(credentials.get("expiresAt"))
    if expiry < 0 or (expiry and expiry <= time.time() + 60):
        return ""
    return value


def renewal_available() -> bool:
    import db

    credentials = _credentials()
    refresh = credentials.get("refreshToken")
    return (isinstance(refresh, str) and 0 < len(refresh) <= 4096
            and _deadline(credentials.get("refreshExpiresAt")) > time.time() + 60
            and credentials.get("clientId") == client_id()
            and type(credentials.get("accountId")) is int
            and credentials["accountId"] == db.get_setup_setting("github_connection", {}).get("id"))


class GitHubRenewalError(ValueError):
    def __init__(self) -> None:
        super().__init__("GitHub credential renewal could not finish. Check the protected credential store or reconnect GitHub in Setup > GitHub.")


async def ensure_access_token() -> str:
    import db
    import setup_auth
    import setup_github

    global _pending_renewal, _renewal_failed_source, _renewal_retry_after
    current = access_token()
    expires_at = _deadline(_credentials().get("expiresAt"))
    if (current and (not expires_at or expires_at > time.time() + 600)) or not renewal_available():
        return current
    async with _renewal_lock:
        current = access_token()
        credentials = _credentials()
        expires_at = _deadline(credentials.get("expiresAt"))
        if (current and (not expires_at or expires_at > time.time() + 600)) or not renewal_available():
            return current
        source = os.environ.get("GALLERY_GITHUB_TOKEN", "").strip()
        if source == _renewal_failed_source and time.time() < _renewal_retry_after:
            raise GitHubRenewalError()
        configuration = db.get_setup_setting("github", {})
        storage = db.get_setup_setting("github_credential", {"secretStorage": "app_service"})
        try:
            if not _pending_renewal or _pending_renewal["source"] != source:
                result = await oauth_request("/login/oauth/access_token", {
                    "client_id": credentials["clientId"], "grant_type": "refresh_token", "refresh_token": credentials["refreshToken"],
                })
                token, refresh = result.get("access_token"), result.get("refresh_token")
                duration, refresh_duration = result.get("expires_in"), result.get("refresh_token_expires_in")
                if (result.get("error") or not isinstance(result.get("token_type"), str) or result["token_type"].lower() != "bearer"
                        or not isinstance(token, str) or not 0 < len(token) <= 4096
                        or not isinstance(refresh, str) or not 0 < len(refresh) <= 4096
                        or type(duration) is not int or not 60 < duration <= 366 * 86400
                        or type(refresh_duration) is not int or not 60 < refresh_duration <= 366 * 86400):
                    raise GitHubRenewalError()
                _pending_renewal = {"source": source, "token": token, "expiresAt": time.time() + duration,
                                    "renewal": {"token": refresh, "expiresAt": time.time() + refresh_duration,
                                                "clientId": credentials["clientId"]}}
            pending = _pending_renewal
            account = await setup_github.github_account(token=pending["token"])
            if account.get("id") != credentials["accountId"]:
                raise GitHubRenewalError()
            await setup_github.verify_reconnection(account, pending["token"])
            if (os.environ.get("GALLERY_GITHUB_TOKEN", "").strip() != source
                    or client_id() != credentials["clientId"]
                    or configuration != db.get_setup_setting("github", {})
                    or storage != db.get_setup_setting("github_credential", {"secretStorage": "app_service"})
                    or pending["expiresAt"] <= time.time() + 60):
                raise GitHubRenewalError()
            await setup_auth.store_github_device_token(pending["token"], pending["expiresAt"], account, storage, pending["renewal"])
            _pending_renewal = None
            _renewal_failed_source = ""
            _renewal_retry_after = 0
            db.record_event(type="github_credential_renewed")
            return access_token()
        except (ValueError, httpx.HTTPError, OSError, TimeoutError, AzureError):
            _renewal_failed_source = source
            _renewal_retry_after = time.time() + 60
            db.record_event(type="github_credential_renewal_failed", meta={"stage": "storage_or_verification" if _pending_renewal else "exchange"})
            raise GitHubRenewalError() from None


async def oauth_request(path: str, data: dict[str, str]) -> dict[str, Any]:
    if path not in ("/login/device/code", "/login/oauth/access_token"):
        raise ValueError("Invalid GitHub authorization endpoint")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async with client.stream("POST", "https://github.com" + path, data=data, headers={"Accept": "application/json"}) as response:
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 16384:
                    raise ValueError("GitHub authorization response exceeds the limit")
            try:
                result = json.loads(content)
            except (ValueError, UnicodeError):
                response.raise_for_status()
                raise ValueError("GitHub returned an invalid authorization response. Retry later; verify the host can reach github.com without an intervening sign-in page.") from None
            if not isinstance(result, dict):
                response.raise_for_status()
                raise ValueError("Invalid GitHub authorization response")
            error = result.get("error")
            if (
                path == "/login/device/code"
                and response.status_code in (200, 400, 401, 403, 404, 422)
                and isinstance(error, str)
                and error in _DEVICE_START_ERRORS
            ):
                raise ValueError(_DEVICE_START_ERRORS[error])
            response.raise_for_status()
    return result


async def start(owner: str, storage: dict | None = None) -> dict:
    configured = client_id()
    if not configured:
        raise ValueError("Save an organization GitHub OAuth App client ID with device flow enabled in Setup")
    now = time.time()
    for identifier, flow in list(_flows.items()):
        if flow.expires_at <= now or flow.owner == owner:
            if flow.lock.locked():
                raise ValueError("A GitHub authorization request is already being processed")
            _flows.pop(identifier, None)
    if len(_flows) >= 20:
        raise ValueError("Too many pending GitHub sign-ins")
    result = await oauth_request("/login/device/code", {"client_id": configured, "scope": _SCOPES})
    if result.get("error"):
        error = result["error"]
        message = _DEVICE_START_ERRORS.get(error) if isinstance(error, str) else None
        raise ValueError(message or "GitHub device sign-in was rejected. Verify the saved OAuth App Client ID, enable Device Flow in that app's settings, and check organization OAuth policy.")
    user_code = str(result.get("user_code", ""))
    device_code = str(result.get("device_code", ""))
    if not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", user_code) or not device_code or len(device_code) > 1024:
        raise ValueError("Invalid GitHub device authorization response")
    if result.get("verification_uri") != "https://github.com/login/device":
        raise ValueError("Unexpected GitHub verification destination")
    interval = max(5, min(60, int(result.get("interval", 5))))
    expiry = max(1, min(900, int(result.get("expires_in", 900))))
    identifier = secrets.token_urlsafe(32)
    _flows[identifier] = DeviceFlow(owner, configured, device_code, now + expiry, interval, now + interval)
    _flows[identifier].storage = storage or {}
    return {"flowId": identifier, "userCode": user_code, "verificationUri": "https://github.com/login/device", "interval": interval, "expiresIn": expiry}


def get_flow(owner: str, identifier: str) -> DeviceFlow:
    flow = _flows.get(identifier)
    if flow is None or flow.owner != owner:
        raise ValueError("No GitHub sign-in exists for this administrator")
    if flow.expires_at <= time.time():
        _flows.pop(identifier, None)
        raise ValueError("GitHub sign-in expired; connect again")
    return flow


async def poll(owner: str, identifier: str) -> dict:
    flow = get_flow(owner, identifier)
    async with flow.lock:
        if flow.token:
            return {"status": "authorized"}
        now = time.time()
        if now < flow.next_poll:
            return {"status": "pending", "interval": max(1, math.ceil(flow.next_poll - now))}
        flow.next_poll = now + flow.interval
        result = await oauth_request("/login/oauth/access_token", {
            "client_id": flow.client_id, "device_code": flow.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        error = result.get("error")
        if error in ("authorization_pending", "slow_down"):
            if error == "slow_down":
                flow.interval = min(120, flow.interval + 5)
            flow.next_poll = time.time() + flow.interval
            return {"status": "pending", "interval": flow.interval}
        if error:
            _flows.pop(identifier, None)
            message = "GitHub authorization was denied" if error == "access_denied" else "GitHub sign-in failed or expired; connect again"
            return {"status": "error", "message": message}
        token = result.get("access_token")
        scopes = set(str(result.get("scope", "")).replace(",", " ").split())
        github_app = isinstance(token, str) and token.startswith("ghu_") and not scopes
        if (not isinstance(token, str) or not token or len(token) > 4096
            or not isinstance(result.get("token_type"), str) or result["token_type"].lower() != "bearer"
            or (not github_app and not {"repo", "read:org"}.issubset(scopes))):
            _flows.pop(identifier, None)
            raise ValueError("GitHub did not grant the required repository and organization permissions")
        if result.get("expires_in") is not None:
            seconds = result["expires_in"]
            if type(seconds) is not int or not 60 < seconds <= 366 * 86400:
                _flows.pop(identifier, None)
                raise ValueError("GitHub token expires too soon; connect again")
            flow.token_expires_at = time.time() + seconds
        if "refresh_token" in result:
            refresh = result["refresh_token"]
            duration = result.get("refresh_token_expires_in")
            if (not isinstance(refresh, str) or not refresh or len(refresh) > 4096
                    or type(duration) is not int or not 60 < duration <= 366 * 86400
                    or not flow.token_expires_at):
                _flows.pop(identifier, None)
                raise ValueError("GitHub returned invalid renewal credentials; connect again")
            flow.refresh_token = refresh
            flow.refresh_expires_at = time.time() + duration
        flow.token = token
        flow.device_code = ""
        return {"status": "authorized"}


def cancel(owner: str, identifier: str) -> None:
    flow = get_flow(owner, identifier)
    if flow.lock.locked():
        raise ValueError("GitHub sign-in is being processed")
    _flows.pop(identifier, None)