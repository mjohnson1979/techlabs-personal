import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

import db
import lifecycle


def notice(identifier="one", **fields):
    return {"id": identifier, "title": "Example notice", "products": ["Azure App Service"],
            "status": "Launched", "generalAvailabilityDate": "2026-09-01", **fields}


def test_classification_dates_deduplication_and_service_mapping():
    current = datetime(2026, 9, 17, tzinfo=timezone.utc)
    data = lifecycle.project([
        notice(previewAvailabilityDate="2026-01-01"),
        notice("duplicate"),
        notice("retiring", title="Retiring plan", availabilities=[{"ring": "Retirement", "year": 2027, "month": "January"}]),
        notice("past", title="Old plan", availabilities=[{"ring": "Retirement", "year": 2025, "month": 1}]),
        notice("preview", title="Preview feature", status="In preview", previewAvailabilityDate="2026-08-01"),
        notice("other", title="Other product", products=["Azure Document Intelligence"]),
        notice("old", title="Old GA", generalAvailabilityDate="2024-01-01"),
    ], current)
    service = data["services"]["appservice"]
    assert len(service["launched"]) == len(service["previews"]) == len(service["retirements"]) == 1
    assert service["retirements"][0]["date"] == "2027-01"
    assert "openai" not in data["services"]
    assert "azure-document-intelligence" in data["services"]


def mock_feed(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


def test_complete_pagination_is_anonymous_and_bounded(monkeypatch):
    calls = []
    def handler(request):
        assert "authorization" not in request.headers
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json={"value": [notice()], "@odata.nextLink": f"https://{lifecycle.FEED_BACKEND}/api/v2/azure/?$skip=100"})
        assert str(request.url).startswith(lifecycle.FEED_URL + "?")
        return httpx.Response(200, json={"value": [notice("two", title="Second notice")]})
    mock_feed(monkeypatch, handler)
    result = asyncio.run(lifecycle.fetch_snapshot())
    assert len(calls) == 2 and result["services"]


@pytest.mark.parametrize("next_link", ["https://evil.test/feed", "http://www.microsoft.com/releasecommunications/api/v2/azure", lifecycle.FEED_URL, "https://www.microsoft.com/other"])
def test_unsafe_or_looping_pagination_is_rejected(monkeypatch, next_link):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={"value": [notice()], "@odata.nextLink": str(request.url) if next_link == lifecycle.FEED_URL else next_link})
    mock_feed(monkeypatch, handler)
    with pytest.raises(ValueError, match="pagination"):
        asyncio.run(lifecycle.fetch_snapshot())
    assert len(calls) == 1


@pytest.mark.parametrize("document", [{"value": []}, {"items": [notice()]}, {"value": [notice()], "@odata.nextLink": "?next=1"}])
def test_empty_malformed_or_truncated_feed_cannot_publish(monkeypatch, document):
    monkeypatch.setattr(lifecycle, "MAX_PAGES", 1)
    mock_feed(monkeypatch, lambda request: httpx.Response(200, json=document))
    with pytest.raises(ValueError):
        asyncio.run(lifecycle.fetch_snapshot())


def test_snapshot_rejects_external_links():
    value = lifecycle.project([notice()])
    value["services"]["appservice"]["launched"][0]["url"] = "javascript:alert(1)"
    with pytest.raises(ValueError, match="link"):
        lifecycle.validate_snapshot(value)


def test_refresh_runs_once_daily_and_retains_last_good_on_failure(monkeypatch):
    db.init_db()
    clock = {"now": datetime(2026, 9, 17, tzinfo=timezone.utc).timestamp()}
    monkeypatch.setattr(lifecycle.time, "time", lambda: clock["now"])
    calls = []
    async def fetch():
        calls.append(True)
        if len(calls) > 1:
            raise ValueError("private upstream details")
        return lifecycle.project([notice()], datetime.fromtimestamp(clock["now"], timezone.utc))
    monkeypatch.setattr(lifecycle, "fetch_snapshot", fetch)
    asyncio.run(lifecycle.refresh())
    first = db.get_setup_setting("lifecycle_snapshot")
    asyncio.run(lifecycle.refresh())
    assert len(calls) == 1
    clock["now"] += 86401
    asyncio.run(lifecycle.refresh())
    assert db.get_setup_setting("lifecycle_snapshot") == first
    assert lifecycle.snapshot()["stale"] is True
    assert lifecycle.snapshot()["job"]["status"] == "failed"
    assert "private" not in str(lifecycle.snapshot())
    asyncio.run(lifecycle.refresh())
    assert len(calls) == 2


@pytest.mark.parametrize("available", [False, True])
def test_runner_choice_and_container_fallback(monkeypatch, available):
    import setup_github
    db.init_db()
    db.set_setup_setting("catalog_processing", {"mode": "github-hosted"})
    db.set_setup_setting("github", {"enabled": True})
    called = []
    async def probe():
        return {"github-hosted": {"available": available}}
    async def fetch():
        called.append(True)
        return lifecycle.project([notice()])
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    monkeypatch.setattr(lifecycle, "fetch_snapshot", fetch)
    asyncio.run(lifecycle.refresh())
    assert called == ([] if available else [True])
    assert lifecycle.snapshot()["job"]["status"] == ("runner" if available else "succeeded")


def test_schedule_runs_on_start_without_manual_trigger(monkeypatch):
    calls = []
    async def run():
        stop = asyncio.Event()
        async def refresh():
            calls.append(True)
            stop.set()
        monkeypatch.setattr(lifecycle, "refresh", refresh)
        await lifecycle.run_schedule(stop)
    asyncio.run(run())
    assert calls == [True]


def test_interrupted_fetch_retains_last_success(monkeypatch):
    db.init_db()
    previous = lifecycle.project([notice()], datetime(2025, 1, 1, tzinfo=timezone.utc))
    db.set_setup_setting("lifecycle_snapshot", previous)
    async def cancel():
        raise asyncio.CancelledError()
    monkeypatch.setattr(lifecycle, "fetch_snapshot", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lifecycle.refresh())
    assert db.get_setup_setting("lifecycle_snapshot") == previous
    assert lifecycle.snapshot()["job"]["status"] == "interrupted"


def test_failed_second_page_does_not_publish_partial_data(monkeypatch):
    db.init_db()
    previous = lifecycle.project([notice()], datetime(2025, 1, 1, tzinfo=timezone.utc))
    db.set_setup_setting("lifecycle_snapshot", previous)
    def handler(request):
        if "$skip" in str(request.url):
            return httpx.Response(503, text="private upstream error")
        return httpx.Response(200, json={"value": [notice("new")], "@odata.nextLink": lifecycle.FEED_URL + "?$skip=100"})
    mock_feed(monkeypatch, handler)
    asyncio.run(lifecycle.refresh())
    assert db.get_setup_setting("lifecycle_snapshot") == previous
    assert lifecycle.snapshot()["job"]["status"] == "failed"
    assert "private" not in str(lifecycle.snapshot())


def test_runner_import_retains_data_on_invalid_or_older_snapshot():
    db.init_db()
    good = lifecycle.project([notice()])
    lifecycle.import_runner(json.dumps(good).encode())
    assert db.get_setup_setting("lifecycle_snapshot") == good
    assert lifecycle.snapshot()["job"]["source"] == "runner"
    invalid = lifecycle.project([notice()])
    invalid["services"]["appservice"]["launched"][0]["url"] = "https://untrusted.test/notice"
    lifecycle.import_runner(json.dumps(invalid).encode())
    assert db.get_setup_setting("lifecycle_snapshot") == good
    assert lifecycle.snapshot()["job"]["status"] == "failed"
    older = {**good, "generated": "2025-01-01T00:00:00+00:00"}
    lifecycle.import_runner(json.dumps(older).encode())
    assert db.get_setup_setting("lifecycle_snapshot") == good


def test_lifecycle_endpoint_requires_user_and_exposes_status_without_caching():
    import api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import CurrentUser, require_user
    db.init_db()
    application = FastAPI()
    application.include_router(api.build_api_router())
    client = TestClient(application)
    assert client.get("/api/lifecycle").status_code == 401
    application.dependency_overrides[require_user] = lambda: CurrentUser("user", "user@example.test", "User", "tenant")
    response = client.get("/api/lifecycle")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["generated"] is None
    assert response.json()["job"]["status"] == "pending"


def test_generated_lifecycle_workflow_has_daily_schedule_and_no_azure_credentials():
    import catalog_workflows
    for labels in (None, ["self-hosted", "Linux", "X64"]):
        workflow = json.loads(catalog_workflows.workflow_files(labels)[".github/workflows/portal-lifecycle.yml"])
        assert workflow["on"]["schedule"] == [{"cron": "47 6 * * *"}]
        assert "pull_request" not in workflow["on"]
        refresh = workflow["jobs"]["refresh"]
        assert refresh["permissions"] == {"contents": "read"}
        assert refresh["runs-on"] == (labels or ["ubuntu-latest"])
        assert any(step.get("run") == "python tools/portal_catalog.py lifecycle" for step in refresh["steps"])
        assert "azure/login" not in str(refresh) and "id-token" not in str(refresh)
        assert workflow["jobs"]["publish"]["needs"] == "refresh"


@pytest.mark.parametrize("valid", [False, True])
def test_catalog_sync_imports_runner_feed_and_retains_it_on_invalid_output(monkeypatch, valid):
    import base64
    import setup_github
    db.init_db()
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    db.set_setup_setting("catalog_processing", {"mode": "github-hosted"})
    prior = lifecycle.project([notice("old", title="Old notice")], datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.set_setup_setting("lifecycle_snapshot", prior)
    newer = lifecycle.project([notice("new", title="New notice")])
    content = json.dumps(newer if valid else {"items": []}).encode()
    async def probe(**kwargs):
        return {"github-hosted": {"available": True}}
    async def request(path, **kwargs):
        if "/git/ref/" in path:
            return {"object": {"sha": "a" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "root"}}
        if path.endswith("/trees/root"):
            return {"tree": [{"path": "site", "type": "tree", "sha": "site"}]}
        if path.endswith("/trees/site"):
            return {"tree": [{"path": "updates.json", "type": "blob", "mode": "100644", "sha": "feed", "size": len(content)}]}
        assert path.endswith("/blobs/feed")
        return {"content": base64.b64encode(content).decode()}
    monkeypatch.setattr(setup_github, "processing_probe", probe)
    monkeypatch.setattr(setup_github, "github_request", request)
    asyncio.run(setup_github.sync_catalog(force=True))
    assert db.get_setup_setting("lifecycle_snapshot") == (newer if valid else prior)
    assert db.get_setup_setting("lifecycle_job")["status"] == ("succeeded" if valid else "failed")


def test_static_lifecycle_feed_is_authenticated_and_database_backed(monkeypatch):
    import app
    import identity
    from fastapi import HTTPException
    from starlette.requests import Request
    db.init_db()
    value = lifecycle.project([notice()])
    db.set_setup_setting("lifecycle_snapshot", value)
    monkeypatch.setattr(app, "is_customer_distribution", lambda: True)
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: None)
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as error:
        asyncio.run(app.serve_gallery(request, "updates.json"))
    assert error.value.status_code == 401
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: identity.CurrentUser("user", "", "", "tenant"))
    response = asyncio.run(app.serve_gallery(request, "updates.json"))
    assert json.loads(response.body)["services"] == value["services"]
    assert response.headers["cache-control"] == "no-store"