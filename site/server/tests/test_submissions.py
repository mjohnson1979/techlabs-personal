"""Offline checks for Bicep and Terraform upload staging."""

import io
import asyncio
import base64
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import db
import submissions
from identity import CurrentUser, require_user


def bundle(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


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


@pytest.mark.parametrize("filename", ["terraform.tfstate", "terraform.tfstate.json", "saved.tfplan", "saved.tfplan.json", ".terraform/providers/cache.json", "../escape.tf", "run.ps1"])
def test_terraform_upload_still_rejects_unsafe_files(stage, filename):
    with pytest.raises(submissions.SubmissionError):
        stage({"terraform/main.tf": b"terraform {}", filename: b"do not publish"})


@pytest.mark.parametrize("files", [{"variables.tf": b""}, {"main.bicep": b""}])
def test_upload_requires_complete_entrypoint(stage, files):
    with pytest.raises(submissions.SubmissionError):
        stage(files)


def test_bicep_upload_keeps_existing_contract(stage):
    result = stage({"main.bicep": b"", "parameters/dev.bicepparam": b"using '../main.bicep'"})
    assert "catalog/example/main.bicep" in result["files"]


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("github_failure", [False, True])
def test_upload_endpoint_preserves_terraform_for_review_and_download(monkeypatch, mixed, github_failure):
    db.init_db()
    application = FastAPI()
    application.include_router(api.build_api_router())
    application.dependency_overrides[require_user] = lambda: CurrentUser("reviewer", "reviewer@example.test", "Reviewer", "tenant", "admin")
    monkeypatch.setattr(api.github_intake, "configured", lambda: True)
    files = {"terraform/main.tf": b"terraform {}\n", "terraform/dev.tfvars": b'location = "eastus"\n'}
    if mixed:
        files.update({"main.bicep": b"", "parameters/dev.bicepparam": b"using '../main.bicep'"})
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
    assert len(pushed) == 1
    with zipfile.ZipFile(io.BytesIO(submissions.bundle_zip(stored))) as archive:
        for name, data in files.items():
            target = f"catalog/{stored['slug']}/{name}"
            assert pushed[0][target] == data
            assert archive.read(target) == data


def test_staged_terraform_inputs_sync_as_terraform_catalog_entry(stage, monkeypatch):
    import setup_github
    import catalog_processing
    async def validate(work, tool, arguments):
        assert tool == "terraform" and arguments[:2] == ["fmt", "-write=false"]
        assert (work / arguments[-1] / "main.tf").read_bytes() == b"terraform {}"
        return ""
    monkeypatch.setattr(catalog_processing, "compile_source", validate)
    staged = stage({"terraform/main.tf": b"terraform {}", "terraform/dev.tfvars": b'location = "eastus"'})
    files = submissions.staged_files(staged)
    async def request(path, *args):
        if "/git/ref/" in path:
            return {"object": {"sha": "a" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "root"}}
        if "/git/blobs/" in path:
            name = path.split("/git/blobs/", 1)[1]
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
    assert result["items"][0]["engines"] == ["terraform"]
    assert result["items"][0]["deployReady"] is True
    target = setup_github.snapshot_root(result["version"]) / "_tf/example"
    assert (target / "main.tf").read_bytes() == b"terraform {}"
    assert (target / "dev.tfvars").read_bytes() == b'location = "eastus"'