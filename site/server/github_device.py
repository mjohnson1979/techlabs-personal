"""Administrator-bound GitHub device authorization; credentials remain server-side."""

from __future__ import annotations

import asyncio
import math
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

_SCOPES = "repo read:org"


@dataclass
class DeviceFlow:
    owner: str
    client_id: str
    device_code: str
    expires_at: float
    interval: int
    next_poll: float
    token: str = ""
    token_expires_at: float = 0
    storage: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_flows: dict[str, DeviceFlow] = {}


def client_id() -> str:
    import db

    value = str(db.get_setup_setting("github_oauth_client", "") or os.environ.get("GALLERY_GITHUB_OAUTH_CLIENT_ID", "")).strip()
    return value if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else ""


def access_token() -> str:
    value = os.environ.get("GALLERY_GITHUB_TOKEN", "").strip()
    if not value or value.startswith("@Microsoft.KeyVault("):
        return ""
    try:
        expiry = float(os.environ.get("GALLERY_GITHUB_TOKEN_EXPIRES_AT", "0") or "0")
    except ValueError:
        return ""
    if not math.isfinite(expiry) or (expiry and expiry <= time.time() + 60):
        return ""
    return value


async def oauth_request(path: str, data: dict[str, str]) -> dict[str, Any]:
    if path not in ("/login/device/code", "/login/oauth/access_token"):
        raise ValueError("Invalid GitHub authorization endpoint")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async with client.stream("POST", "https://github.com" + path, data=data, headers={"Accept": "application/json"}) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 16384:
                    raise ValueError("GitHub authorization response exceeds the limit")
    import json
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("Invalid GitHub authorization response")
    return result


async def start(owner: str, storage: dict | None = None) -> dict:
    configured = client_id()
    if not configured:
        raise ValueError("Save a customer GitHub OAuth App client ID with device flow enabled in Setup")
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
        raise ValueError("GitHub device sign-in is unavailable; verify the customer OAuth App configuration")
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
        if not isinstance(token, str) or not token or len(token) > 4096 or result.get("token_type", "").lower() != "bearer" or not {"repo", "read:org"}.issubset(scopes):
            _flows.pop(identifier, None)
            raise ValueError("GitHub did not grant the required repository and organization permissions")
        if result.get("expires_in") is not None:
            seconds = int(result["expires_in"])
            if seconds <= 60:
                _flows.pop(identifier, None)
                raise ValueError("GitHub token expires too soon; connect again")
            flow.token_expires_at = time.time() + seconds
        flow.token = token
        flow.device_code = ""
        return {"status": "authorized"}


def cancel(owner: str, identifier: str) -> None:
    flow = get_flow(owner, identifier)
    if flow.lock.locked():
        raise ValueError("GitHub sign-in is being processed")
    _flows.pop(identifier, None)