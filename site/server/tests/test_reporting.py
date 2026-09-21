"""Offline reporting scope and operational risk counts."""

import db
import pytest


@pytest.fixture
def managed_catalog(monkeypatch):
    import api
    import identity
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    db.init_db()
    snapshot = {"repository": "organization/portal", "version": "a" * 40, "items": [
        {"path": "example", "displayName": "Example", "category": "Compute", "deployReady": True, "catalogVersion": "a" * 40},
        {"path": "remaining", "displayName": "Remaining"},
    ]}
    db.set_setup_setting("catalog_snapshot", snapshot)
    monkeypatch.setattr(api, "is_customer_distribution", lambda: True)
    current = {"user": None}
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: current["user"])
    application = FastAPI()
    application.include_router(api.build_api_router())
    return TestClient(application), current, snapshot


def test_catalog_remove_restore_requires_admin_confirmation_and_current_snapshot(managed_catalog):
    from identity import CurrentUser
    client, current, snapshot = managed_catalog
    path = "/api/admin/catalog/example"
    body = {"repository": snapshot["repository"], "version": snapshot["version"], "removed": True, "confirm": True}
    headers = {"Origin": "http://testserver"}
    assert client.get("/api/admin/catalog").status_code == 401
    assert client.put(path, headers=headers, json=body).status_code == 401
    current["user"] = CurrentUser("guest", "", "", "", "guest")
    assert client.get("/api/admin/catalog").status_code == 403
    assert client.put(path, headers=headers, json=body).status_code == 403
    current["user"] = CurrentUser("admin", "admin@example.test", "", "", "admin")
    result = client.get("/api/admin/catalog")
    assert result.headers["cache-control"] == "no-store"
    assert len(result.json()["items"]) == 2
    assert client.put(path, json=body).status_code == 403
    assert client.put(path, headers={"Origin": "https://elsewhere.test"}, json=body).status_code == 403
    assert client.put(path, headers=headers, json={**body, "confirm": False}).status_code == 400
    assert client.put(path, headers=headers, json={**body, "removed": "true"}).status_code == 422
    assert client.put(path, headers=headers, json={**body, "repository": "organization/other"}).status_code == 409
    assert client.put(path, headers=headers, json={**body, "version": "b" * 40}).status_code == 409
    assert client.put("/api/admin/catalog/unknown", headers=headers, json=body).status_code == 404
    assert client.put("/api/admin/catalog/invalid.name", headers=headers, json=body).status_code == 400
    assert db.get_setup_setting("catalog_removed") is None
    assert client.put(path, headers=headers, json=body).json()["changed"] is True
    assert client.put(path, headers=headers, json=body).json()["changed"] is False
    rows = client.get("/api/admin/catalog").json()["items"]
    assert rows[0]["removed"] is True and rows[0]["removedAt"]
    assert db.get_setup_setting("catalog_snapshot") == snapshot
    assert client.put(path, headers=headers, json={**body, "removed": False}).json()["changed"] is True
    assert db.get_setup_setting("catalog_removed") == {}
    events = db.init_db().execute("SELECT type FROM events WHERE type LIKE 'catalog_use_case_%' ORDER BY id").fetchall()
    assert [row["type"] for row in events] == ["catalog_use_case_removed", "catalog_use_case_restored"]


def test_removed_use_case_rejects_stale_deploy_requests_and_preserves_existing(managed_catalog):
    import api
    from identity import CurrentUser
    client, current, snapshot = managed_catalog
    current["user"] = CurrentUser("admin", "", "", "", "admin")
    identifier = "11111111-1111-4111-8111-111111111111"
    db.set_setup_setting("subscriptions", [{"subscriptionId": identifier}])
    db.create_deployment(deployment_id="existing", use_case="example", environment="dev", user_oid="admin",
                         user_upn="", user_name="", subscription_id=identifier, resource_group="existing", end_at=2_000_000_000,
                         params={"catalogVersion": snapshot["version"]})
    before = db.get_deployment("existing")
    response = client.put("/api/admin/catalog/example", headers={"Origin": "http://testserver"},
                          json={"repository": snapshot["repository"], "version": snapshot["version"], "removed": True, "confirm": True})
    assert response.status_code == 200
    assert "example" not in api._catalog_items()
    response = client.post("/api/deployments", headers={"Origin": "http://testserver"},
                           json={"useCase": "example", "environment": "dev", "subscriptionId": identifier, "location": "eastus"})
    assert response.status_code == 404
    assert db.get_deployment("existing") == before
    assert len(db.list_deployments()) == 1


def test_catalog_removal_can_be_restored_after_source_disappears(managed_catalog, tmp_path):
    from identity import CurrentUser
    import setup_github
    client, current, snapshot = managed_catalog
    current["user"] = CurrentUser("admin", "", "", "", "admin")
    artifact = setup_github.snapshot_root(snapshot["version"]) / "_templates/example/dev.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"template":{}}')
    headers = {"Origin": "http://testserver"}
    body = {"repository": snapshot["repository"], "version": snapshot["version"], "removed": True, "confirm": True}
    assert client.put("/api/admin/catalog/example", headers=headers, json=body).status_code == 200
    report = db.telemetry_summary()["catalog"]
    assert report["itemCount"] == report["removedCount"] == 1
    db.set_setup_setting("catalog_snapshot", {**snapshot, "items": [], "version": "b" * 40})
    row = client.get("/api/admin/catalog").json()["items"][0]
    assert row["removed"] and not row["inSnapshot"] and row["displayName"] == "Example"
    assert client.put("/api/admin/catalog/example", headers=headers, json={**body, "version": "b" * 40, "removed": False}).status_code == 200
    assert setup_github.visible_catalog_snapshot()["items"] == []
    assert artifact.read_text() == '{"template":{}}'


def test_removal_during_preflight_does_not_create_deployment(managed_catalog, monkeypatch):
    import api
    from identity import CurrentUser
    client, current, snapshot = managed_catalog
    current["user"] = CurrentUser("admin", "", "", "", "admin")
    identifier = "11111111-1111-4111-8111-111111111111"
    db.set_setup_setting("subscriptions", [{"subscriptionId": identifier}])
    monkeypatch.setattr(api, "_resolve_end_at", lambda *args: 2_000_000_000)
    async def locations(*args):
        return [{"name": "eastus"}]
    async def check(*args, **kwargs):
        db.set_setup_setting("catalog_removed", {snapshot["repository"]: {"example": {"removedAt": 100}}})
        return {"checkedAt": 100, "capacity": {"status": "unknown"}, "priceDetail": "Unavailable"}
    monkeypatch.setattr(api.setup_cloud, "subscription_locations", locations)
    monkeypatch.setattr(api.catalog_processing, "check_region", check)
    monkeypatch.setattr(api.az, "schedule", lambda *args: pytest.fail("Removed workload must not be scheduled"))
    response = client.post("/api/deployments", headers={"Origin": "http://testserver"}, json={
        "useCase": "example", "environment": "dev", "subscriptionId": identifier, "location": "eastus", "lifetimeHours": 1,
    })
    assert response.status_code == 409
    assert "no longer available" in response.json()["detail"]
    assert db.list_deployments() == []


def test_catalog_removal_filters_public_and_deployment_views_without_erasing_source(monkeypatch):
    import asyncio
    import json
    import api
    import app
    import identity
    import setup_github
    from starlette.requests import Request
    db.init_db()
    snapshot = {"repository": "organization/portal", "version": "a" * 40,
                "items": [{"path": "example"}, {"path": "remaining"}]}
    db.set_setup_setting("catalog_snapshot", snapshot)
    db.set_setup_setting("catalog_removed", {"organization/portal": {"example": {"removedAt": 100}}})
    monkeypatch.setattr(api, "is_customer_distribution", lambda: True)
    monkeypatch.setattr(app, "is_customer_distribution", lambda: True)
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: identity.CurrentUser("guest", "", "", "", "guest"))
    assert list(api._catalog_items()) == ["remaining"]
    response = asyncio.run(app.serve_gallery(Request({"type": "http", "headers": []}), "catalog.json"))
    assert json.loads(response.body)["items"] == [{"path": "remaining"}]
    assert response.headers["cache-control"] == "no-store"
    assert setup_github.catalog_snapshot() == snapshot
    db.set_setup_setting("catalog_snapshot", {**snapshot, "version": "b" * 40})
    assert list(api._catalog_items()) == ["remaining"]
    db._CONN.close()
    db._CONN = None
    assert list(api._catalog_items()) == ["remaining"]
    db.set_setup_setting("catalog_snapshot", {**snapshot, "repository": "organization/other"})
    assert list(api._catalog_items()) == ["example", "remaining"]


@pytest.fixture
def removal_repository(managed_catalog, monkeypatch):
    import copy
    import httpx
    import setup_github
    client, current, snapshot = managed_catalog
    snapshot["sourceCommit"] = "b" * 40
    db.set_setup_setting("catalog_snapshot", snapshot)
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    def entry(path, kind, sha):
        return {"path": path, "type": kind, "mode": "040000" if kind == "tree" else "100644", "sha": sha * 40}
    state = {"trees": {
        "c" * 40: [entry("catalog", "tree", "d"), entry("modules", "tree", "e"), entry("site", "tree", "f"), entry("LICENSE", "blob", "1")],
        "d" * 40: [entry("example", "tree", "2"), entry("example-other", "tree", "3"), entry("notes.md", "blob", "4")],
        "2" * 40: [entry("main.bicep", "blob", "5"), entry("parameters", "tree", "6")],
        "6" * 40: [entry("dev.bicepparam", "blob", "7")],
    }, "commits": {"b" * 40: {"tree": {"sha": "c" * 40}, "parents": []}},
        "refs": {"main": "b" * 40}, "calls": [], "permissions": True, "failure": "", "baseReads": 0}
    async def request(path, method="GET", body=None, **kwargs):
        state["calls"].append((path, method, copy.deepcopy(body)))
        if path == "/graphql":
            assert method == "POST"
            inputs = body["variables"]["input"]
            assert inputs["branch"] == {"repositoryNameWithOwner": "organization/portal", "branchName": "main"}
            assert inputs["expectedHeadOid"] == "b" * 40
            assert inputs["fileChanges"] == {"deletions": [
                {"path": "catalog/example/main.bicep"}, {"path": "catalog/example/parameters/dev.bicepparam"}]}
            assert "createCommitOnBranch" in body["query"]
            if state["failure"] in ("protected", "concurrent"):
                if state["failure"] == "concurrent": state["refs"]["main"] = "9" * 40
                return {"errors": [{"type": "UNPROCESSABLE", "message": "private upstream explanation"}]}
            assert inputs["expectedHeadOid"] == state["refs"]["main"]
            remaining = [entry for entry in state["trees"]["d" * 40] if entry["path"] != "example"]
            updated_root = [entry for entry in state["trees"]["c" * 40] if entry["path"] != "catalog"]
            if remaining:
                state["trees"]["5" * 40] = remaining
                updated_root.append(entry("catalog", "tree", "5"))
            state["trees"]["6" * 40] = updated_root
            state["commits"]["7" * 40] = {"tree": {"sha": "6" * 40}, "parents": [{"sha": "b" * 40}]}
            state["refs"]["main"] = "7" * 40
            if state["failure"] == "lost_commit_response":
                state["failure"] = ""
                raise httpx.ReadTimeout("private network detail")
            if state["failure"] == "unrelated_result": state["trees"]["6" * 40] = []
            return {"data": {"createCommitOnBranch": {"commit": {"oid": "7" * 40}}}}
        root = "/repos/organization/portal"
        assert path.startswith(root)
        tail = path[len(root):]
        if not tail:
            return {"full_name": "organization/portal", "default_branch": "main", "permissions": {"push": state["permissions"]}}
        if method == "GET" and tail.startswith("/git/ref/heads/"):
            branch = tail.removeprefix("/git/ref/heads/")
            if branch == "main":
                state["baseReads"] += 1
                if state["failure"] == "drift" and state["baseReads"] > 1:
                    state["refs"]["main"] = "9" * 40
            if branch not in state["refs"]:
                httpx.Response(404, request=httpx.Request("GET", "https://api.github.com" + path)).raise_for_status()
            return {"ref": "refs/heads/" + branch, "object": {"sha": state["refs"][branch]}}
        if method == "GET" and tail.startswith("/git/commits/"):
            return copy.deepcopy(state["commits"][tail.rsplit("/", 1)[1]])
        if method == "GET" and tail.startswith("/git/trees/"):
            return {"tree": copy.deepcopy(state["trees"][tail.rsplit("/", 1)[1]]), "truncated": state["failure"] == "truncated"}
        pytest.fail(f"Unexpected repository operation: {method} {tail}")
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(setup_github.github_device, "access_token", lambda: "test-only-token")
    import asyncio
    monkeypatch.setattr(setup_github, "_SYNC_WAKE", asyncio.Event())
    return state, snapshot


@pytest.mark.parametrize("last_folder", [False, True])
def test_direct_repository_deletion_commits_exact_folder_and_updates_portal(removal_repository, last_folder):
    import asyncio
    import setup_github
    state, snapshot = removal_repository
    if last_folder: state["trees"]["d" * 40] = state["trees"]["d" * 40][:1]
    db.set_setup_setting("catalog_removed", {snapshot["repository"]: {"example": {"removedAt": 100}, "unrelated": {"removedAt": 99}}})
    result = asyncio.run(setup_github.delete_catalog_repository_folder("example", snapshot["repository"], snapshot["version"], "main", snapshot["sourceCommit"]))
    assert result["status"] == "deleted" and result["changed"] and result["deletedFiles"] == 2
    assert result["commit"] == state["refs"]["main"] == "7" * 40
    assert result["url"] == "https://github.com/organization/portal/commit/" + "7" * 40
    assert set(state["refs"]) == {"main"}
    assert [(path, method) for path, method, _ in state["calls"] if method != "GET"] == [("/graphql", "POST")]
    assert [item["path"] for item in setup_github.catalog_snapshot()["items"]] == ["remaining"]
    assert db.get_setup_setting("catalog_removed")[snapshot["repository"]] == {"unrelated": {"removedAt": 99}}
    assert db.get_setup_setting("catalog_job")["status"] == "queued"


@pytest.mark.parametrize("failure", ["protected", "concurrent", "drift", "truncated", "permission", "lost_commit_response", "unrelated_result"])
def test_direct_repository_deletion_failure_has_no_local_success(removal_repository, failure):
    import asyncio
    import setup_github
    state, snapshot = removal_repository
    state["failure"] = failure
    if failure == "permission": state["permissions"] = False
    with pytest.raises((setup_github.CatalogRepositoryFailure, setup_github.CatalogRepositoryConflict)) as error:
        asyncio.run(setup_github.delete_catalog_repository_folder("example", snapshot["repository"], snapshot["version"], "main", snapshot["sourceCommit"]))
    assert "private" not in str(error.value)
    assert db.get_setup_setting("catalog_snapshot") == snapshot and db.get_setup_setting("catalog_removed") is None
    assert db.get_setup_setting("catalog_job") is None
    if failure in ("lost_commit_response", "unrelated_result"):
        assert "may already have committed" in str(error.value)
        writes = len([call for call in state["calls"] if call[1] != "GET"])
        with pytest.raises(setup_github.CatalogRepositoryConflict):
            asyncio.run(setup_github.delete_catalog_repository_folder("example", snapshot["repository"], snapshot["version"], "main", snapshot["sourceCommit"]))
        assert len([call for call in state["calls"] if call[1] != "GET"]) == writes
    elif failure != "concurrent" and failure != "drift":
        assert state["refs"]["main"] == snapshot["sourceCommit"]
    assert set(state["refs"]) == {"main"}


def test_direct_repository_deletion_already_absent_only_reconciles_local_view(removal_repository):
    import asyncio
    import setup_github
    state, snapshot = removal_repository
    state["trees"]["d" * 40] = state["trees"]["d" * 40][1:]
    result = asyncio.run(setup_github.delete_catalog_repository_folder("example", snapshot["repository"], snapshot["version"], "main", snapshot["sourceCommit"]))
    assert result["status"] == "deleted" and not result["changed"]
    assert all(method == "GET" for _, method, _ in state["calls"])
    assert "example" not in {item["path"] for item in setup_github.catalog_snapshot()["items"]}


@pytest.mark.parametrize("case", ["files", "depth", "submodule", "traversal", "duplicate", "no_token"])
def test_direct_repository_deletion_limits_fail_before_mutation(removal_repository, monkeypatch, case):
    import asyncio
    import setup_github
    state, snapshot = removal_repository
    if case == "files":
        state["trees"]["2" * 40] = [{"path": f"file-{index}.txt", "type": "blob", "mode": "100644", "sha": "8" * 40} for index in range(501)]
    elif case == "depth":
        state["trees"]["2" * 40] = [{"path": "nested", "type": "tree", "mode": "040000", "sha": "2" * 40}]
    elif case == "submodule":
        state["trees"]["2" * 40][0].update(type="commit", mode="160000")
    elif case == "traversal":
        state["trees"]["2" * 40][0]["path"] = "../other"
    elif case == "duplicate":
        state["trees"]["2" * 40].append(dict(state["trees"]["2" * 40][0]))
    else:
        monkeypatch.setattr(setup_github.github_device, "access_token", lambda: "")
    with pytest.raises((setup_github.CatalogRepositoryConflict, setup_github.GitHubConnectionError)):
        asyncio.run(setup_github.delete_catalog_repository_folder("example", snapshot["repository"], snapshot["version"], "main", snapshot["sourceCommit"]))
    assert all(method == "GET" for _, method, _ in state["calls"])
    assert state["refs"]["main"] == snapshot["sourceCommit"]
    assert db.get_setup_setting("catalog_snapshot") == snapshot


@pytest.mark.parametrize("failure", ["truncated", "permission", "symlink", "drift", "snapshot", "selection", "source", "branch"])
def test_repository_removal_fails_closed_before_publishing(removal_repository, failure):
    import asyncio
    import setup_github
    state, snapshot = removal_repository
    state["failure"] = failure
    if failure == "permission": state["permissions"] = False
    if failure == "missing": state["trees"]["d" * 40] = []
    if failure == "symlink": state["trees"]["d" * 40][0].update(type="blob", mode="120000")
    if failure == "selection": db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "other", "branch": "main"})
    with pytest.raises(setup_github.CatalogRepositoryConflict):
        asyncio.run(setup_github.delete_catalog_repository_folder("example", snapshot["repository"],
            "f" * 40 if failure == "snapshot" else snapshot["version"], "other" if failure == "branch" else "main",
            "f" * 40 if failure == "source" else snapshot["sourceCommit"]))
    assert all(call[1] == "GET" for call in state["calls"])


def test_repository_removal_api_requires_admin_origin_confirmation_and_throttle(managed_catalog, monkeypatch):
    import api
    import setup_github
    from identity import CurrentUser
    client, current, snapshot = managed_catalog
    path = "/api/admin/catalog/example/repository-removal"
    body = {"action": "delete_from_repository", "repository": snapshot["repository"], "version": snapshot["version"], "branch": "main", "sourceCommit": "b" * 40, "confirm": True}
    headers = {"Origin": "http://testserver"}
    calls = []
    async def remove(*arguments):
        calls.append(arguments)
        return {"status": "deleted", "changed": True, "branch": "main", "commit": "c" * 40, "deletedFiles": 2,
                "url": "https://github.com/organization/portal/commit/" + "c" * 40}
    monkeypatch.setattr(setup_github, "delete_catalog_repository_folder", remove)
    assert client.post(path, headers=headers, json=body).status_code == 401
    current["user"] = CurrentUser("guest", "", "", "", "guest")
    assert client.post(path, headers=headers, json=body).status_code == 403
    current["user"] = CurrentUser("admin", "", "", "", "admin")
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, headers={"Origin": "https://elsewhere.test"}, json=body).status_code == 403
    assert client.post(path, headers=headers, json={**body, "confirm": False}).status_code == 400
    assert client.post(path, headers=headers, json={**body, "confirm": "true"}).status_code == 422
    assert client.post(path, headers=headers, json={**body, "sourceCommit": "invalid"}).status_code == 422
    assert client.post(path, headers=headers, json={key: value for key, value in body.items() if key != "action"}).status_code == 422
    assert client.post(path, headers=headers, json={**body, "action": "review"}).status_code == 422
    assert not calls
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert calls == [("example", snapshot["repository"], snapshot["version"], "main", "b" * 40)]
    assert client.post(path, headers=headers, json=body).status_code == 429
    assert db.get_setup_setting("catalog_removed") is None and db.get_setup_setting("catalog_snapshot") == snapshot
    assert db.init_db().execute("SELECT COUNT(*) FROM events WHERE type='catalog_repository_use_case_deleted'").fetchone()[0] == 1


@pytest.mark.parametrize("kind,status", [("conflict", 409), ("authorization", 400), ("transport", 502), ("timeout", 502), ("github", 502)])
def test_repository_removal_api_failure_never_hides_use_case(managed_catalog, monkeypatch, kind, status):
    import httpx
    import setup_github
    from identity import CurrentUser
    client, current, snapshot = managed_catalog
    current["user"] = CurrentUser("admin", "", "", "", "admin")
    async def remove(*arguments):
        errors = {"conflict": setup_github.CatalogRepositoryConflict("The repository branch changed."),
                  "authorization": setup_github.GitHubConnectionError(), "transport": httpx.ConnectError("private token"),
                  "timeout": TimeoutError("private path"), "github": setup_github.CatalogRepositoryFailure("delete", status=403)}
        raise errors[kind]
    monkeypatch.setattr(setup_github, "delete_catalog_repository_folder", remove)
    result = client.post("/api/admin/catalog/example/repository-removal", headers={"Origin": "http://testserver"}, json={
        "action": "delete_from_repository", "repository": snapshot["repository"], "version": snapshot["version"], "branch": "main", "sourceCommit": "b" * 40, "confirm": True,
    })
    assert result.status_code == status and "private" not in result.text
    assert db.get_setup_setting("catalog_removed") is None
    assert db.get_setup_setting("catalog_snapshot") == snapshot
    if kind == "github":
        assert "committing the folder deletion" in result.text and "HTTP 403" in result.text
        assert db.init_db().execute("SELECT COUNT(*) FROM events WHERE type='catalog_repository_deletion_failed'").fetchone()[0] == 1


def test_admin_use_case_ui_confirms_changes_and_preserves_state_on_failure():
        import shutil
        import subprocess
        from pathlib import Path
        node = shutil.which("node")
        if not node:
                pytest.skip("Node.js is required for catalog management UI tests")
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/admin.js','utf8');
const code = source.slice(source.indexOf('  function renderCatalog('),source.indexOf('  function renderTelemetry('));
const nodes = [];
function element(tag,props={},children=[]) {
    const node = {tag,children:Array.isArray(children)?children:[children],listeners:{},value:'',disabled:false,isConnected:true,...props,
        classList:{add(){},toggle(){}}, setAttribute(name,value){this[name]=value;if(name==='aria-label')this.label=value;}, append(...items){this.children.push(...items)},appendChild(child){this.children.push(child)},
        replaceChildren(...items){this.children=items},addEventListener(event,action){this.listeners[event]=action}};
    nodes.push(node); return node;
}
let approved = false, fail = false, writes = [], events = [], messages = [], repoWrites = [], unsafeLink = false;
let items = [{path:'example',displayName:'Example',category:'Compute',removed:false,inSnapshot:true},
                         {path:'other',displayName:'Other',category:'Data',removed:false,inSnapshot:true}];
const context = {el:element,URL,
    searchControl(label){return element('input',{'aria-label':label})},
    toolButton(symbol,label,action){return element('button',{text:symbol,label,action})},
    fmtDate:value=>value, confirm(message){messages.push(message);return approved},
    document:{dispatchEvent(event){events.push(event.type)},createTextNode(text){return element('text',{text})}},CustomEvent:function(type){this.type=type},
    Api:{async adminCatalog(){return {supported:true,repositoryRemovalAvailable:true,branch:'main',sourceCommit:'b'.repeat(40),repository:'organization/portal',version:'a'.repeat(40),items:items.map(item=>({...item}))}},
        async requestCatalogRepositoryRemoval(path,repo,version,branch,sourceCommit){repoWrites.push({path,repo,version,branch,sourceCommit});if(fail)throw new Error('Repository denied');if(!unsafeLink)items=items.filter(item=>item.path!==path);return {status:'deleted',path,branch,commit:'c'.repeat(40),url:unsafeLink?'https://evil.test/review':'https://github.com/organization/portal/commit/'+'c'.repeat(40)}},
        async setCatalogRemoved(path,repo,version,removed){writes.push({path,repo,version,removed});if(fail)throw new Error('Simulated server failure');items=items.map(item=>item.path===path?{...item,removed,removedAt:removed?100:null}:item)}}
};
vm.createContext(context);vm.runInContext(code,context);
const mount=element('div');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function descendants(node){return [node,...(node.children||[]).filter(child=>child&&typeof child==='object').flatMap(descendants)]}
function action(label){return descendants(mount).find(node=>node.label===label)}
(async()=>{
 context.renderCatalog(mount); await flush();
 const filter=nodes.find(node=>node['aria-label']==='Use-case visibility');
 const search=nodes.find(node=>node['aria-label']==='Search use cases');
 const status=nodes.find(node=>node.role==='status');
 await action('Remove from portal: Example').action();
 assert.equal(writes.length,0);assert.match(messages.at(-1),/NOT be deleted/);
 approved=true;fail=true;
 await action('Remove from portal: Example').action();
 assert.equal(events.length,0);assert.match(status.textContent,/Simulated server failure/);
 assert.ok(action('Remove from portal: Example')); assert.equal(action('Refresh use cases').disabled,false);
 fail=false;
 const pending=action('Remove from portal: Example').action();
 assert.equal(action('Remove from portal: Example').disabled,true);
 await pending;
 assert.equal(writes.at(-1).repo,'organization/portal'); assert.equal(writes.at(-1).version,'a'.repeat(40));
 assert.equal(writes.at(-1).removed,true);assert.deepEqual(events,['techlabs:catalog-changed']);
 assert.equal(action('Remove from portal: Example'),undefined);
 filter.value='removed';filter.listeners.change();
 assert.ok(action('Restore to portal: Example'));assert.equal(action('Remove from portal: Other'),undefined);
 search.value='absent';search.listeners.input();assert.equal(action('Restore to portal: Example'),undefined);
 search.value='';search.listeners.input();
 await action('Restore to portal: Example').action();
 assert.equal(writes.at(-1).removed,false);assert.match(messages.at(-1),/existing deployments are unchanged/);
 assert.equal(action('Restore to portal: Example'),undefined);
 filter.value='published';filter.listeners.change();
 assert.ok(action('Remove from portal: Example'));assert.equal(events.length,2);
 assert.match(status.textContent,/Removal cleared/);
 const selectRepository=()=>{const choice=descendants(mount).find(node=>node['aria-label']==='Removal action for Example');choice.value='repository';choice.listeners.change();};
 selectRepository();approved=false;
 await action('Remove from repository: Example').action();assert.equal(repoWrites.length,0);
 assert.match(messages.at(-1),/catalog\/example\/.*ALL files.*directly from organization\/portal \/ main now/);
 assert.match(messages.at(-1),/not a review branch/);
 approved=true;fail=true;
 await action('Remove from repository: Example').action();assert.match(status.textContent,/Repository denied/);
 assert.equal(events.length,2);assert.equal(items[0].removed,false);
 fail=false;unsafeLink=true;selectRepository();
 await action('Remove from repository: Example').action();assert.match(status.textContent,/Repository deletion could not be verified/);
 assert.equal(descendants(status).some(node=>node.tag==='a'),false);
 unsafeLink=false;selectRepository();
 await action('Remove from repository: Example').action();
 const link=descendants(status).find(node=>node.tag==='a');assert.ok(link.href.startsWith('https://github.com/organization/portal/commit/'));
 assert.match(descendants(status).find(node=>node.tag==='text').text,/catalog\/example\/ is absent.*Removed from the portal/);
 assert.equal(events.length,3);assert.equal(items.some(item=>item.path==='example'),false);
 assert.equal(action('Remove from portal: Example'),undefined);
 assert.equal(repoWrites.at(-1).sourceCommit,'b'.repeat(40));assert.equal(repoWrites.at(-1).branch,'main');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr


def test_reporting_separates_current_risk_from_period_activity(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(db.time, "time", lambda: now)
    connection = db.init_db()
    rows = [
        ("old-failure", "failed", now - 40 * 86400, now - 86400, "delete_resource_group"),
        ("overdue", "active", now - 86400, now - 60, "delete_resource_group"),
        ("external", "active", now - 86400, now - 60, "none"),
        ("ending", "active", now - 86400, now + 60, "delete_resource_group"),
        ("destroyed", "destroyed", now - 86400, now - 60, "delete_resource_group"),
        ("destroying", "destroying", now - 86400, now - 60, "delete_resource_group"),
    ]
    for identifier, status, created, end, policy in rows:
        db.create_deployment(deployment_id=identifier, use_case="example", environment="dev",
                             user_oid="operator", user_upn="operator@example.test", user_name="Operator",
                             subscription_id="subscription", resource_group="example", end_at=end, cleanup_policy=policy)
        connection.execute("UPDATE deployments SET created_at = ?, status = ? WHERE id = ?", (created, status, identifier))
    connection.commit()
    db.record_event(type="view", user_oid="operator")
    db.record_event(type="view", user_oid="operator")
    db.record_event(type="catalog_job")
    result = db.telemetry_summary(7)
    assert result["periodDeployments"] == 5
    assert "failed" not in result["periodStatusCounts"]
    assert result["statusCounts"]["failed"] == 1
    assert result["operations"] == {"failed": 1, "overdue": 1, "expiringSoon": 1, "inProgress": 1, "externallyManaged": 1}
    assert result["uniqueUsers"] == 1
    assert result["since"] == now - 7 * 86400
    assert result["generatedAt"] == now


def test_reporting_counts_all_submissions_and_limits_catalog_metadata():
    connection = db.init_db()
    connection.executemany("INSERT INTO submissions (id, slug, status, created_at) VALUES (?, ?, ?, ?)",
                           [(str(index), "example", "branched" if index == 0 else "pending", 1.0) for index in range(205)])
    connection.commit()
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    db.set_setup_setting("catalog_snapshot", {"syncedAt": 1.0, "items": [{"name": "example", "internal": "not-for-summary"}]})
    db.set_setup_setting("catalog_job", {"status": "failed", "detail": "Validation failed."})
    db.set_setup_setting("github_credential", {"secretSettingName": "NOT_FOR_REPORT"})
    result = db.telemetry_summary()
    assert result["submissionCounts"] == {"pending": 204, "branched": 1}
    assert result["catalog"]["repository"] == "organization/portal"
    assert result["catalog"]["itemCount"] == 1
    assert result["catalog"]["job"]["status"] == "failed"
    assert "not-for-summary" not in str(result)
    assert "NOT_FOR_REPORT" not in str(result)


def test_reporting_empty_state_has_zero_counts():
    db.init_db()
    result = db.telemetry_summary()
    assert result["periodDeployments"] == result["uniqueUsers"] == 0
    assert all(value == 0 for value in result["operations"].values())
    assert result["submissionCounts"] == {}
    assert result["catalog"]["repository"] == ""
    assert result["catalog"]["syncedAt"] is None