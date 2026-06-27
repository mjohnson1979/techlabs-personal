"""Guard tests for the Azure deployment executor's SDK usage.

These catch the class of failures that previously only surfaced at *runtime*
inside App Service:

* ``cannot import name 'ResourceManagementClient' from 'azure.mgmt.resource'``
* ``'ResourceManagementClient' object has no attribute 'deployments'``

Both were caused by an azure-mgmt-resource version whose client layout differs
from what the executor expects. By constructing the real client (with a fake
credential, no network calls) and asserting the exact operation groups the
executor calls, CI fails *before* deploying instead of after.

Run: ``python -m pytest site/server/tests`` (azure-identity + azure-mgmt-resource
from site/requirements.txt must be installed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import azure_deploy  # noqa: E402


class _FakeCredential:
    """A credential that satisfies the SDK constructor without authenticating."""

    def get_token(self, *scopes, **kwargs):  # pragma: no cover - never called here
        from azure.core.credentials import AccessToken

        return AccessToken("fake", 9999999999)


def test_executor_available() -> None:
    ok, reason = azure_deploy.executor_available()
    assert ok, f"Azure SDK should be importable: {reason}"


def test_client_exposes_operation_groups_used_by_executor(monkeypatch) -> None:
    """The executor calls client.resource_groups / deployments /
    deployment_operations. A wrong SDK version drops these and the deploy fails
    only at runtime — assert they exist at construction time instead."""
    monkeypatch.setattr(azure_deploy, "_get_credential", lambda: _FakeCredential())

    client = azure_deploy._client("00000000-0000-0000-0000-000000000000")

    for attr in ("resource_groups", "deployments", "deployment_operations"):
        assert hasattr(client, attr), (
            f"ResourceManagementClient is missing '{attr}'. The installed "
            "azure-mgmt-resource version is incompatible with the executor "
            "(expected the unified 23.x client). Check site/requirements.txt."
        )


def test_deployments_client_has_required_methods(monkeypatch) -> None:
    """Assert the specific methods the executor invokes are present."""
    monkeypatch.setattr(azure_deploy, "_get_credential", lambda: _FakeCredential())

    client = azure_deploy._client("00000000-0000-0000-0000-000000000000")

    assert hasattr(client.deployments, "begin_create_or_update")
    assert hasattr(client.deployment_operations, "list")
    assert hasattr(client.resource_groups, "create_or_update")
    assert hasattr(client.resource_groups, "begin_delete")
    assert hasattr(client.resource_groups, "get")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
