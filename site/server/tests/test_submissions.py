"""Offline checks for Bicep and Terraform upload staging."""

import io
import asyncio
import base64
import zipfile
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import db
import github_intake
import identity
import submissions
from identity import CurrentUser, require_admin, require_user


def bundle(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def supporting_document(extension: str) -> bytes:
    if extension == ".ps1":
        return b"throw 'Review-only script must never execute'\r\n"
    parts = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}
    if extension in parts:
        return bundle({"[Content_Types].xml": b"<Types/>", "_rels/.rels": b"<Relationships/>", parts[extension]: b"<document/>"})
    return {".pdf": b"%PDF-1.7\n%%EOF", ".csv": b"name,value\nexample,1\n", ".tsv": b"name\tvalue\nexample\t1\n"}[extension]


@pytest.fixture
def stage(tmp_path, monkeypatch):
    monkeypatch.setattr(submissions, "_staging_root", lambda: tmp_path)
    monkeypatch.setattr(submissions, "_unique_slug", lambda slug: slug)

    def upload(files):
        return submissions.stage_submission(data=bundle(files), display_name="Example",
                                            user_oid="reviewer", user_upn="reviewer@example.test", user_name="Reviewer")
    return upload


@pytest.mark.parametrize("prefix", ["", "workload/", "terraform/", "workload/terraform/"])
def test_terraform_only_upload_normalizes_without_changing_content(stage, prefix):
    files = {"main.tf": b'terraform {}\n', "variables.tf": b'variable "location" { type = string }\n',
             "dev.tfvars": b'location = "eastus"\n', "prod.tfvars": b'location = "westus"\n',
             "dev.tfvars.json": b'{"location":"eastus"}'}
    result = stage({prefix + name: data for name, data in files.items()})
    root = Path(result["path"]) / "catalog/example"
    for name, data in files.items():
        assert (root / "terraform" / name).read_bytes() == data
    assert not (root / "main.bicep").exists()
    assert (root / "metadata.json").is_file()
    assert "Terraform" in (root / "README.md").read_text()


def test_mixed_upload_accepts_exact_reported_tfvars_path(stage):
    files = {"main.bicep": b"", "parameters/dev.bicepparam": b"using '../main.bicep'",
             "terraform/main.tf": b"terraform {}", "terraform/dev.tfvars": b'location = "eastus"'}
    result = stage(files)
    root = Path(result["path"]) / "catalog/example"
    for name, data in files.items():
        assert (root / name).read_bytes() == data


@pytest.mark.parametrize("extension", sorted(submissions.DOCUMENT_EXT))
@pytest.mark.parametrize("folder", ["", "workload/docs/"])
def test_supporting_documents_preserved_in_staging_and_download(stage, extension, folder):
    document = supporting_document(extension)
    files = {"main.tf": b"terraform {}", folder + "Operator Guide" + extension.upper(): document}
    result = stage(files)
    expected = "catalog/example/" + folder + "Operator Guide" + extension.upper()
    assert submissions.staged_files(result)[expected] == document
    with zipfile.ZipFile(io.BytesIO(submissions.bundle_zip(result))) as archive:
        assert archive.read(expected) == document


@pytest.mark.parametrize("extension", [".docm", ".xlsm", ".pptm", ".doc", ".xls", ".ppt", ".exe", ".js", ".sh", ".bat", ".zip"])
def test_supporting_documents_do_not_allow_other_scripts_or_macro_formats(stage, extension):
    with pytest.raises(submissions.SubmissionError, match="File type not allowed"):
        stage({"main.tf": b"terraform {}", "document" + extension: b"untrusted"})


@pytest.mark.parametrize("extension", [".docx", ".xlsx", ".pptx", ".pdf"])
def test_supporting_document_requires_matching_format(stage, extension):
    with pytest.raises(submissions.SubmissionError, match="Invalid"):
        stage({"main.tf": b"terraform {}", "document" + extension: b"not a document"})


def test_macro_document_cannot_be_renamed_to_docx(stage):
    document = bundle({"[Content_Types].xml": b"<Types/>", "_rels/.rels": b"<Relationships/>",
                       "word/document.xml": b"<document/>", "word/vbaProject.bin": b"macro"})
    with pytest.raises(submissions.SubmissionError, match="Macro-enabled"):
        stage({"main.tf": b"terraform {}", "document.docx": document})


@pytest.mark.parametrize("case", ["wrong_package", "missing_part", "macro_type", "prefixed_executable", "expanded_limit", "content_types_limit"])
def test_office_attachment_rejects_invalid_or_oversized_packages(stage, case):
    parts = {"[Content_Types].xml": b"<Types/>", "_rels/.rels": b"<Relationships/>", "word/document.xml": b"<document/>"}
    if case == "wrong_package":
        parts["xl/workbook.xml"] = parts.pop("word/document.xml")
    elif case == "missing_part":
        del parts["_rels/.rels"]
    elif case == "macro_type":
        parts["[Content_Types].xml"] = b'<Types ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/>'
    elif case == "content_types_limit":
        parts["[Content_Types].xml"] = b"x" * (256 * 1024 + 1)
    if case == "expanded_limit":
        parts["word/document.xml"] = b"x" * (submissions.MAX_TOTAL_UNCOMPRESSED + 1)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
        document = buffer.getvalue()
    else:
        document = bundle(parts)
    if case == "prefixed_executable":
        document = b"MZ" + document
    with pytest.raises(submissions.SubmissionError):
        stage({"main.tf": b"terraform {}", "document.docx": document})


@pytest.mark.parametrize("extension", [".csv", ".tsv"])
@pytest.mark.parametrize("content", [b"MZ\x00binary", b"\xff\xffbinary"])
def test_tabular_attachment_rejects_binary_content(stage, extension, content):
    with pytest.raises(submissions.SubmissionError, match="tabular documents"):
        stage({"main.tf": b"terraform {}", "document" + extension: content})


def test_tabular_attachment_preserves_utf16(stage):
    content = "name\tvalue\nexample\t1".encode("utf-16")
    result = stage({"main.tf": b"terraform {}", "data.tsv": content})
    assert submissions.staged_files(result)["catalog/example/data.tsv"] == content


@pytest.mark.parametrize("filename", ["terraform.tfstate", "terraform.tfstate.json", "saved.tfplan", "saved.tfplan.json", ".terraform/providers/cache.json", "../escape.tf", "../run.ps1", "scripts/../../run.ps1", "/run.ps1"])
def test_terraform_upload_still_rejects_unsafe_files(stage, filename):
    with pytest.raises(submissions.SubmissionError):
        stage({"terraform/main.tf": b"terraform {}", filename: b"do not publish"})


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
@pytest.mark.parametrize("wrapper", ["", "workload/"])
def test_powershell_helper_preserved_without_execution(stage, encoding, wrapper):
    content = "throw 'Review-only script must never execute'\r\n".encode(encoding)
    result = stage({wrapper + "main.tf": b"terraform {}", wrapper + "scripts/Set-SreDemoScenario.PS1": content})
    name = "catalog/example/scripts/Set-SreDemoScenario.PS1"
    assert submissions.staged_files(result)[name] == content
    with zipfile.ZipFile(io.BytesIO(submissions.bundle_zip(result))) as archive:
        assert archive.read(name) == content


@pytest.mark.parametrize("content", [b"MZ\x00binary", b"\xff\xffbinary"])
def test_powershell_helper_rejects_binary_content(stage, content):
    with pytest.raises(submissions.SubmissionError, match="PowerShell attachments"):
        stage({"main.tf": b"terraform {}", "scripts/run.ps1": content})


@pytest.mark.parametrize("files", [{"variables.tf": b""}, {"main.bicep": b""}])
def test_upload_requires_complete_entrypoint(stage, files):
    with pytest.raises(submissions.SubmissionError):
        stage(files)


def test_bicep_upload_keeps_existing_contract(stage):
    result = stage({"main.bicep": b"", "parameters/dev.bicepparam": b"using '../main.bicep'"})
    assert "catalog/example/main.bicep" in result["files"]


@pytest.mark.parametrize("engine", ["bicep", "terraform", "both"])
@pytest.mark.parametrize("github_failure", [False, True])
def test_upload_endpoint_preserves_supplied_formats_for_review_and_download(monkeypatch, engine, github_failure):
    db.init_db()
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("reviewer", "reviewer@example.test", "Reviewer", "tenant", "guest")
    monkeypatch.setattr(api.github_intake, "configured", lambda: True)
    files = {}
    if engine in ("terraform", "both"):
        files.update({"terraform/main.tf": b"terraform {}\n", "terraform/dev.tfvars": b'location = "eastus"\n'})
    if engine in ("bicep", "both"):
        files.update({"main.bicep": b"", "parameters/dev.bicepparam": b"using '../main.bicep'"})
    files.update({"docs/Operator Guide" + extension: supporting_document(extension) for extension in submissions.REVIEW_ONLY_EXT})
    pushed = []
    async def push(*, branch, files, message):
        pushed.append(files)
        if github_failure:
            raise RuntimeError("offline GitHub failure")
        return {"branch": branch, "commit": "a" * 40, "compareUrl": "https://github.com/organization/portal/compare/review"}
    monkeypatch.setattr(api.github_intake, "push_submission", push)
    response = TestClient(application).post("/api/use-cases/upload", headers={"Origin": "http://testserver"},
                                          files={"file": ("example.zip", bundle(files), "application/zip")}, data={"displayName": "Example"})
    assert response.status_code == 200
    stored = db.get_submission(response.json()["submission"]["id"])
    assert stored["status"] == ("pending" if github_failure else "branched")
    assert response.json()["stagedOnly"] is github_failure
    if github_failure:
        assert response.json()["detail"] == stored["detail"]
        assert "could not be confirmed" in response.json()["detail"]
        assert "will not be sent automatically" in response.json()["detail"]
        assert "offline GitHub failure" not in response.text
    assert len(pushed) == 1
    assert db.list_deployments() == []
    assert (f"catalog/{stored['slug']}/main.bicep" in pushed[0]) == (engine in ("bicep", "both"))
    assert (f"catalog/{stored['slug']}/terraform/main.tf" in pushed[0]) == (engine in ("terraform", "both"))
    with zipfile.ZipFile(io.BytesIO(submissions.bundle_zip(stored))) as archive:
        for name, data in files.items():
            target = f"catalog/{stored['slug']}/{name}"
            assert pushed[0][target] == data
            assert archive.read(target) == data


@pytest.mark.parametrize("connection", ["missing", "expired", "disabled"])
def test_upload_without_usable_github_explicitly_stays_staged(monkeypatch, connection):
    import time
    db.init_db()
    monkeypatch.setenv("GALLERY_DISTRIBUTION", "organization")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN", "test-only-token" if connection != "missing" else "")
    monkeypatch.setenv("GALLERY_GITHUB_TOKEN_EXPIRES_AT", str(time.time() - 60 if connection == "expired" else 0))
    db.set_setup_setting("github", {"enabled": connection != "disabled", "owner": "organization", "repository": "portal", "branch": "main"})
    async def unexpected(**kwargs):
        pytest.fail("An unavailable GitHub connection must not attempt a push")
    monkeypatch.setattr(github_intake, "push_submission", unexpected)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("guest", "", "Guest", "tenant", "guest")
    response = TestClient(application).post("/api/use-cases/upload", headers={"Origin": "http://testserver"},
        files={"file": ("example.zip", bundle({"main.tf": b"terraform {}"}), "application/zip")}, data={"displayName": "Example"})
    assert response.status_code == 200
    stored = db.get_submission(response.json()["submission"]["id"])
    assert stored["status"] == "pending" and not stored["branch"]
    assert response.json()["stagedOnly"] is True
    assert response.json()["validationFailed"] is False
    assert response.json()["detail"] == stored["detail"]
    assert "No review branch was created" in stored["detail"]
    assert "will not be sent automatically" in stored["detail"]
    assert "Setup > GitHub" in stored["detail"]
    assert "queued" not in response.text
    assert "test-only-token" not in response.text
    assert submissions.bundle_zip(stored)
    assert db.list_deployments() == []


def test_upload_panel_distinguishes_staging_from_github_review():
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for upload UI tests")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('site/assets/app.js', 'utf8');
const start = source.indexOf('  function openSubmitPanel()');
const end = source.indexOf('  function wireSubmitNav()', start);
assert.ok(start >= 0 && end > start);
async function check(response, expectedButton, expectedClass, disabled) {
    const nodes = [];
    function element(tag, props = {}, children = []) {
        const classes = new Set();
        const node = {tag, ...props, children: [...children], value: '', listeners: {},
            classList: {add(...items){items.forEach(item => classes.add(item));}, remove(...items){items.forEach(item => classes.delete(item));}},
            appendChild(child){this.children.push(child);}, addEventListener(event, handler){this.listeners[event] = handler;}, classes};
        nodes.push(node); return node;
    }
    let calls = 0;
    const context = {closeModal(){}, document: {createTextNode(text){return {text};}},
        window: {TechlabsPanel: {open(callback){callback(element('body'), element);}},
            TechlabsApi: {async uploadUseCase(){calls++; return response;}}}};
    vm.runInNewContext(source.slice(start, end), context);
    context.openSubmitPanel();
    nodes.find(node => node.id === 'submit-zip').files = [{name: 'example.zip', size: 100}];
    const button = nodes.find(node => node.class === 'submit-btn');
    button.listeners.click();
    await new Promise(setImmediate);
    const status = nodes.find(node => node.class === 'submit-status');
    assert.equal(calls, 1);
    assert.equal(button.textContent, expectedButton);
    assert.equal(button.disabled, disabled);
    assert.ok(status.classes.has(expectedClass));
    assert.ok(!status.classes.has(expectedClass === 'ok' ? 'err' : 'ok'));
    assert.ok(status.children.some(child => child.text === 'Submission: example'));
    if (response.detail) assert.equal(status.children[0].text, response.detail);
    else assert.match(status.children[0].text, /Saved in the portal only/);
}
(async () => {
    await check({submission: {slug: 'example', status: 'pending'}, stagedOnly: true, detail: 'Reconnect GitHub.'}, 'Saved in portal', 'err', true);
    await check({submission: {slug: 'example', status: 'pending'}}, 'Saved in portal', 'err', true);
    await check({submission: {slug: 'example', status: 'pending'}, validationFailed: true, detail: 'Validation failed.'}, 'Upload corrected template', 'err', false);
    await check({submission: {slug: 'example', status: 'branched'}, stagedOnly: false, detail: 'Review branch created.'}, 'Uploaded \u2713', 'ok', true);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_submission_validation_does_not_fetch_prices(stage, monkeypatch):
    import json
    import catalog_processing
    calls = []
    async def validate(work, tool, arguments):
        calls.append(tool)
        return ""
    async def price(*args, **kwargs):
        pytest.fail("Submission validation must not fetch prices")
    monkeypatch.setattr(catalog_processing, "compile_source", validate)
    monkeypatch.setattr(catalog_processing.pricing, "estimate", price)
    staged = stage({"terraform/main.tf": b"terraform {}", "metadata.json": json.dumps({
        "displayName": "Example", "cost": {"components": [{"service": "Compute"}]},
    }).encode()})
    files = submissions.staged_files(staged)
    result = asyncio.run(catalog_processing.process_blobs(files, refresh_prices=False))
    assert calls == ["terraform"]
    assert not any(name.endswith("pricing.json") for name in result)


def test_submission_documents_are_not_compiler_inputs_or_charged_to_source_limits(monkeypatch):
    import catalog_processing
    import setup_github
    sources = {"catalog/example/metadata.json": b'{"displayName":"Example"}',
               "catalog/example/terraform/main.tf": b"terraform {}"}
    attachments = {f"catalog/example/docs/Guide {number}.PDF": b"%PDF-1.7\n" + b"x" * (3 * 1024 * 1024) for number in range(5)}
    attachments["catalog/example/scripts/Set-SreDemoScenario.PS1"] = b"# " + b"x" * (3 * 1024 * 1024)
    files = {**sources, **attachments}
    before = dict(files)
    async def request(path, **kwargs):
        assert "/git/trees/" in path
        return {"tree": [], "truncated": False}
    async def process(blobs, *, refresh_prices):
        assert blobs == sources and refresh_prices is False
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(catalog_processing, "process_blobs", process)
    asyncio.run(github_intake.validate_submission_sources(files, "organization/portal", "b" * 40, "test-only-token"))
    assert files == before


@pytest.mark.parametrize("oversize", ["source", "document", "script", "total"])
def test_submission_document_support_preserves_size_limits(monkeypatch, oversize):
    import setup_github
    files = {"catalog/example/metadata.json": b'{"displayName":"Example"}'}
    if oversize == "source":
        files["catalog/example/main.bicep"] = b"x" * (2 * 1024 * 1024 + 1)
    elif oversize == "document":
        files["catalog/example/docs/guide.pdf"] = b"x" * (submissions.MAX_SINGLE_FILE + 1)
    elif oversize == "script":
        files["catalog/example/scripts/run.ps1"] = b"# " + b"x" * submissions.MAX_SINGLE_FILE
    else:
        files.update({f"catalog/example/docs/{number}.pdf": b"x" * submissions.MAX_SINGLE_FILE for number in range(5)})
    async def unexpected(*args, **kwargs):
        pytest.fail("Size limits must be checked before remote calls")
    monkeypatch.setattr(setup_github, "github_request", unexpected)
    with pytest.raises(github_intake.SubmissionValidationError, match="limits"):
        asyncio.run(github_intake.validate_submission_sources(files, "organization/portal", "b" * 40, "test-only-token"))


@pytest.mark.parametrize("failure", ["", "validation", "drift", "timeout"])
def test_review_branch_validates_before_any_github_write(monkeypatch, failure):
    import distribution
    calls = []
    monkeypatch.setattr(distribution, "is_customer_distribution", lambda: True)
    monkeypatch.setattr(github_intake, "_base", lambda: "main")
    def handler(request):
        calls.append(request.method)
        if request.url.path.endswith("/git/ref/heads/main"):
            changed = failure == "drift" and "validated" in calls
            return httpx.Response(200, json={"object": {"sha": ("d" if changed else "a") * 40}})
        if request.url.path.endswith("/git/commits/" + "a" * 40):
            return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
        assert "validated" in calls
        return httpx.Response(201, json={"sha": "c" * 40})
    mock_branch_api(monkeypatch, handler)
    async def validate(files, repo, tree, token):
        assert calls == ["GET", "GET"]
        assert tree == "b" * 40 and token == "test-only-token"
        if failure == "timeout":
            raise TimeoutError()
        if failure == "validation":
            raise github_intake.SubmissionValidationError("Missing local module (BCP091).")
        calls.append("validated")
    monkeypatch.setattr(github_intake, "validate_submission_sources", validate)
    task = github_intake.push_submission(branch="usecase-intake/example-abcdef", files={"catalog/example/main.bicep": b"source"}, message="Review")
    if failure:
        with pytest.raises(github_intake.SubmissionValidationError):
            asyncio.run(task)
        assert "POST" not in calls
    else:
        assert asyncio.run(task)["branch"] == "usecase-intake/example-abcdef"
        assert calls[:4] == ["GET", "GET", "validated", "GET"]


def test_submission_validation_combines_pinned_dependencies_and_staged_files(monkeypatch):
    import setup_github
    import catalog_processing
    files = {"catalog/example/metadata.json": b'{"displayName":"Example"}', "catalog/example/main.bicep": b"new template"}
    calls = []
    async def request(path, **kwargs):
        calls.append(path)
        assert kwargs["token"] == "test-only-token"
        if "/git/trees/" in path:
            return {"truncated": False, "tree": [
                {"path": "modules/shared/main.bicep", "mode": "100644", "type": "blob", "sha": "c" * 40},
                {"path": "catalog/example/main.bicep", "mode": "100644", "type": "blob", "sha": "d" * 40},
                {"path": "catalog/other/main.bicep", "mode": "100644", "type": "blob", "sha": "e" * 40},
            ]}
        return {"content": base64.b64encode(b"shared module").decode()}
    async def process(sources, *, refresh_prices):
        assert refresh_prices is False
        assert sources == {**files, "modules/shared/main.bicep": b"shared module"}
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(catalog_processing, "process_blobs", process)
    asyncio.run(github_intake.validate_submission_sources(files, "organization/portal", "b" * 40, "test-only-token"))
    assert len(calls) == 2


def test_failed_upload_validation_is_visible_and_keeps_download(monkeypatch):
    db.init_db()
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("guest", "", "Guest", "", "guest")
    monkeypatch.setattr(github_intake, "configured", lambda: True)
    async def push(**kwargs):
        raise github_intake.SubmissionValidationError("Missing local module (BCP091).")
    monkeypatch.setattr(github_intake, "push_submission", push)
    response = TestClient(application).post("/api/use-cases/upload", headers={"Origin": "http://testserver"},
        files={"file": ("example.zip", bundle({"terraform/main.tf": b"terraform {}"}), "application/zip")})
    assert response.status_code == 200
    assert response.json()["validationFailed"] is True
    assert "BCP091" in response.json()["detail"]
    stored = db.get_submission(response.json()["submission"]["id"])
    assert stored["status"] == "pending" and not stored["branch"]
    assert "BCP091" in stored["detail"]
    assert submissions.bundle_zip(stored)


@pytest.mark.parametrize("case", ["truncated", "symlink", "submodule", "oversize", "malformed", "input_limit", "compiler"])
def test_submission_validation_rejects_unverifiable_sources(monkeypatch, case):
    import setup_github
    import catalog_processing
    files = {"catalog/example/metadata.json": b'{"displayName":"Example"}'}
    if case == "input_limit":
        files["catalog/example/main.bicep"] = b"x" * (2 * 1024 * 1024 + 1)
    async def request(path, **kwargs):
        assert case != "input_limit"
        entry = {"path": "modules/shared/main.bicep", "mode": "100644", "type": "blob", "size": 1, "sha": "c" * 40}
        if case == "symlink": entry["mode"] = "120000"
        if case == "submodule": entry["type"] = "commit"
        if case == "oversize": entry["size"] = 2 * 1024 * 1024 + 1
        return {"truncated": case == "truncated", "tree": [None if case == "malformed" else entry] if case != "compiler" else []}
    async def process(*args, **kwargs):
        assert case == "compiler"
        raise catalog_processing.CatalogValidationError("bicep", "catalog/example/main.bicep", ["BCP091"])
    monkeypatch.setattr(setup_github, "github_request", request)
    monkeypatch.setattr(catalog_processing, "process_blobs", process)
    with pytest.raises(github_intake.SubmissionValidationError) as error:
        asyncio.run(github_intake.validate_submission_sources(files, "organization/portal", "b" * 40, "test-only-token"))
    if case == "compiler": assert "BCP091" in str(error.value)


@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_staged_single_format_inputs_sync_with_only_uploaded_engine(stage, monkeypatch, engine):
    import json
    import setup_github
    import catalog_processing
    template = {"parameters": {
        "location": {"type": "string", "defaultValue": "eastus"},
        "minReplicas": {"type": "int", "defaultValue": 3, "minValue": 1, "maxValue": 5, "metadata": {"description": "Minimum replicas"}},
        "appServiceSku": {"type": "string", "defaultValue": "B1", "allowedValues": ["B1", "S1"]},
        "password": {"type": "secureString", "defaultValue": "must-not-be-published"},
    }, "resources": []}
    validated = []
    async def validate(work, tool, arguments):
        validated.append(tool)
        assert tool == engine
        if tool == "terraform":
            assert arguments[:2] == ["fmt", "-write=false"]
            assert (work / arguments[-1] / "main.tf").read_bytes() == b"terraform {}"
            return ""
        if arguments[0] == "build":
            return json.dumps(template)
        assert arguments[0] == "build-params"
        return json.dumps({"templateJson": json.dumps(template), "parametersJson": '{"parameters":{}}'})
    monkeypatch.setattr(catalog_processing, "compile_source", validate)
    uploaded = {"terraform/main.tf": b"terraform {}", "terraform/dev.tfvars": b'location = "eastus"'} if engine == "terraform" else {
        "main.bicep": b"param location string = 'eastus'", "parameters/dev.bicepparam": b"using '../main.bicep'",
    }
    uploaded.update({"docs/Operator Guide" + extension: supporting_document(extension) for extension in submissions.REVIEW_ONLY_EXT})
    staged = stage(uploaded)
    files = submissions.staged_files(staged)
    async def request(path, *args):
        if "/git/ref/" in path:
            return {"object": {"sha": "a" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "root"}}
        if "/git/blobs/" in path:
            name = path.split("/git/blobs/", 1)[1]
            assert Path(name).suffix.lower() not in submissions.REVIEW_ONLY_EXT
            return {"content": base64.b64encode(files[name]).decode()}
        folder = path.split("/git/trees/", 1)[1]
        prefix = "" if folder == "root" else folder + "/"
        entries = {}
        for name, data in files.items():
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix):]
            child = relative.split("/", 1)[0]
            entries[child] = {"path": child, "sha": prefix + child, "type": "tree"} if "/" in relative else {
                "path": child, "sha": name, "type": "blob", "mode": "100644", "size": len(data),
            }
        return {"tree": list(entries.values())}
    monkeypatch.setattr(setup_github, "github_request", request)
    db.init_db()
    db.set_setup_setting("github", {"enabled": True, "owner": "organization", "repository": "portal", "branch": "main"})
    result = asyncio.run(setup_github.sync_catalog())
    assert result["items"][0]["engines"] == [engine]
    assert result["items"][0]["deployReady"] is True
    assert validated and set(validated) == {engine}
    assert submissions.staged_files(staged) == files
    root = setup_github.snapshot_root(result["version"])
    assert not any(path.suffix.lower() in submissions.REVIEW_ONLY_EXT for path in root.rglob("*"))
    if engine == "terraform":
        assert (root / "_tf/example/main.tf").read_bytes() == uploaded["terraform/main.tf"]
        assert (root / "_tf/example/dev.tfvars").read_bytes() == uploaded["terraform/dev.tfvars"]
        assert not (root / "_templates/example").exists()
    else:
        assert json.loads((root / "_templates/example/dev.json").read_bytes())["template"] == template
        assert not (root / "_tf/example").exists()
        assert result["items"][0]["parameters"] == [
            {"name": "minReplicas", "type": "int", "default": 3, "min": 1, "max": 5, "description": "Minimum replicas"},
            {"name": "appServiceSku", "type": "string", "default": "B1", "allowed": ["B1", "S1"]},
        ]
        assert api._validate_parameter_values({"minReplicas": "4", "appServiceSku": "S1"}, result["items"][0]) == {"minReplicas": "4", "appServiceSku": "S1"}


@pytest.fixture
def branched_submission(stage):
    staged = stage({"terraform/main.tf": b"terraform {}"})
    branch = f"usecase-intake/{staged['slug']}-{staged['id'][:6]}"
    db.init_db()
    db.create_submission(submission_id=staged["id"], slug=staged["slug"],
                         display_name="Example", user_oid="reviewer", user_upn="reviewer@example.test",
                         user_name="Reviewer", path=staged["path"], files=staged["files"])
    db.update_submission(staged["id"], status="branched", branch=branch,
                         compare_url=f"https://github.com/organization/original/compare/main...{branch}?expand=1")
    return db.get_submission(staged["id"])


def mock_branch_api(monkeypatch, handler):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(github_intake, "_token", lambda: "test-only-token")
    monkeypatch.setattr(github_intake, "_repo", lambda: "organization/different")
    monkeypatch.setattr(github_intake.httpx, "AsyncClient",
                        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))


def test_attachment_bytes_reach_only_the_review_branch(stage, monkeypatch):
    import json
    import distribution
    import catalog_processing
    import setup_github
    documents = {"docs/Operator Guide" + extension: supporting_document(extension) for extension in submissions.REVIEW_ONLY_EXT}
    staged = stage({"terraform/main.tf": b"terraform {}", **documents})
    files = submissions.staged_files(staged)
    blobs = []
    requests = []
    async def compiler(work, tool, arguments):
        assert tool == "terraform"
        assert not any(path.suffix.lower() in submissions.REVIEW_ONLY_EXT for path in work.rglob("*"))
        return ""
    async def tree(path, **kwargs):
        assert path.endswith("/git/trees/" + "b" * 40)
        return {"tree": [], "truncated": False}
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            if request.url.path.endswith("/git/ref/heads/main"):
                return httpx.Response(200, json={"object": {"sha": "a" * 40}})
            assert request.url.path.endswith("/git/commits/" + "a" * 40)
            return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
        assert request.method == "POST"
        payload = json.loads(request.content)
        if request.url.path.endswith("/git/blobs"):
            blobs.append(base64.b64decode(payload["content"], validate=True))
        elif request.url.path.endswith("/git/trees"):
            assert {entry["path"] for entry in payload["tree"]} == set(files)
            assert all(entry["mode"] == "100644" for entry in payload["tree"])
        elif request.url.path.endswith("/git/commits"):
            assert payload["parents"] == ["a" * 40]
        else:
            assert request.url.path.endswith("/git/refs")
            assert payload["ref"] == "refs/heads/usecase-intake/example-abcdef"
        return httpx.Response(201, json={"sha": "c" * 40})
    monkeypatch.setattr(distribution, "is_customer_distribution", lambda: True)
    monkeypatch.setattr(github_intake, "_base", lambda: "main")
    monkeypatch.setattr(catalog_processing, "compile_source", compiler)
    monkeypatch.setattr(setup_github, "github_request", tree)
    mock_branch_api(monkeypatch, handler)
    result = asyncio.run(github_intake.push_submission(branch="usecase-intake/example-abcdef", files=files, message="Review"))
    assert result["branch"] == "usecase-intake/example-abcdef"
    assert blobs == list(files.values())
    assert all(content in blobs for content in documents.values())
    assert sum(request.url.path.endswith("/git/refs") for request in requests) == 1


@pytest.mark.parametrize("missing", [False, True])
def test_delete_original_review_branch_even_after_repository_change(monkeypatch, branched_submission, missing):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.url.path.startswith("/repos/organization/original")
        if request.method == "DELETE":
            assert request.url.path.endswith("/git/refs/heads/" + branched_submission["branch"])
            return httpx.Response(204)
        if "/branches/" in request.url.path:
            return httpx.Response(404 if missing else 200, json={"protected": False})
        return httpx.Response(200, json={"full_name": "organization/original", "default_branch": "main", "permissions": {"push": True}})
    mock_branch_api(monkeypatch, handler)
    asyncio.run(github_intake.delete_submission_branch(branched_submission))
    assert sum(request.method == "DELETE" for request in requests) == (0 if missing else 1)


@pytest.mark.parametrize("failure", ["default", "protected", "denied", "timeout", "read-only"])
def test_branch_delete_failure_keeps_submission(monkeypatch, branched_submission, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private diagnostic", request=request)
        if request.method == "DELETE":
            assert failure == "denied"
            return httpx.Response(403, json={"message": "private diagnostic"})
        if "/branches/" in request.url.path:
            return httpx.Response(200, json={"protected": failure == "protected"})
        return httpx.Response(200, json={"full_name": "organization/original", "permissions": {"push": failure != "read-only"},
                              "default_branch": branched_submission["branch"] if failure == "default" else "main"})
    mock_branch_api(monkeypatch, handler)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "admin@example.test", "Admin", "tenant", "admin")
    response = TestClient(application).post(f"/api/admin/submissions/{branched_submission['id']}/discard",
                                           headers={"Origin": "http://testserver"})
    assert response.status_code == 409
    assert "private diagnostic" not in response.text
    assert db.get_submission(branched_submission["id"])["status"] == "branched"
    assert Path(branched_submission["path"]).is_dir()


def test_delete_submission_removes_branch_before_files_and_is_idempotent(monkeypatch, branched_submission):
    removed = []
    async def delete(submission):
        assert Path(submission["path"]).is_dir()
        removed.append(submission["branch"])
    monkeypatch.setattr(github_intake, "delete_submission_branch", delete)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "admin@example.test", "Admin", "tenant", "admin")
    client = TestClient(application)
    endpoint = f"/api/admin/submissions/{branched_submission['id']}/discard"
    assert client.post(endpoint, headers={"Origin": "https://other.test"}).status_code == 403
    assert not removed
    assert client.post(endpoint, headers={"Origin": "http://testserver"}).status_code == 204
    assert client.post(endpoint, headers={"Origin": "http://testserver"}).status_code == 204
    assert removed == [branched_submission["branch"]]
    stored = db.get_submission(branched_submission["id"])
    assert stored["status"] == "discarded" and stored["compare_url"] == ""
    assert not Path(branched_submission["path"]).exists()


@pytest.mark.parametrize("field,value", [("branch", "main"), ("compare_url", "https://other.test/org/repo/compare/main...branch"),
                                        ("compare_url", ""), ("branch", "")])
def test_unverified_branch_deletion_never_calls_github(monkeypatch, branched_submission, field, value):
    branched_submission[field] = value
    monkeypatch.setattr(github_intake, "_token", lambda: pytest.fail("Unexpected credential access"))
    with pytest.raises(github_intake.BranchDeletionError):
        asyncio.run(github_intake.delete_submission_branch(branched_submission))


def test_guest_cannot_delete_submission(monkeypatch, branched_submission):
    async def unexpected(submission):
        pytest.fail("Guests must not reach GitHub deletion")
    monkeypatch.setattr(github_intake, "delete_submission_branch", unexpected)
    application = FastAPI()
    application.include_router(api.build_api_router())
    monkeypatch.setattr(identity, "_resolve_identity", lambda request: CurrentUser("guest", "guest@example.test", "Guest", "tenant", "guest"))
    response = TestClient(application).post(f"/api/admin/submissions/{branched_submission['id']}/discard", headers={"Origin": "http://testserver"})
    assert response.status_code == 403
    assert Path(branched_submission["path"]).is_dir()


def test_unbranched_submission_deletion_needs_no_github_token(monkeypatch, branched_submission):
    db.update_submission(branched_submission["id"], status="pending", branch="", compare_url="")
    monkeypatch.setattr(github_intake, "_token", lambda: pytest.fail("Local deletion must not access GitHub credentials"))
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "admin@example.test", "Admin", "tenant", "admin")
    response = TestClient(application).post(f"/api/admin/submissions/{branched_submission['id']}/discard", headers={"Origin": "http://testserver"})
    assert response.status_code == 204
    assert not Path(branched_submission["path"]).exists()


def test_local_file_cleanup_failure_keeps_record_for_retry(monkeypatch, branched_submission):
    async def delete(submission):
        return None
    def deny(path):
        raise PermissionError("private filesystem path")
    monkeypatch.setattr(github_intake, "delete_submission_branch", delete)
    monkeypatch.setattr(submissions.shutil, "rmtree", deny)
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_admin] = lambda: CurrentUser("admin", "admin@example.test", "Admin", "tenant", "admin")
    response = TestClient(application).post(f"/api/admin/submissions/{branched_submission['id']}/discard", headers={"Origin": "http://testserver"})
    assert response.status_code == 409
    assert "branch may already be deleted" in response.json()["detail"]
    assert "private filesystem path" not in response.text
    assert db.get_submission(branched_submission["id"])["status"] == "branched"
    assert Path(branched_submission["path"]).is_dir()