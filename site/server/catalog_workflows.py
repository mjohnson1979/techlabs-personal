"""Generate opt-in workflows for a verified Linux runner selection."""

import json


def workflow_files(labels: list[str] | None = None) -> dict[str, bytes]:
    runner = labels or ["ubuntu-latest"]
    checkout = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    upload = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    download = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    branch = "${{ vars.PORTAL_CATALOG_BRANCH || github.event.repository.default_branch }}"
    outputs = {}
    for capacity in (False, True):
        name = "portal-capacity" if capacity else "portal-catalog"
        tools = {"name": "Check runner tools", "run": "python --version\naz version\ngit --version"}
        trigger = {"workflow_dispatch": {}, "schedule": [{"cron": "37 6 * * *" if capacity else "17 5 * * *"}]}
        if not capacity:
            trigger["push"] = {"paths": ["catalog/**", "modules/**", "tools/portal_catalog.py", "site/server/pricing.py", ".github/workflows/portal-catalog.yml"]}
        steps = [{"name": "Checkout approved catalog branch", "uses": checkout, "with": {"ref": branch, "persist-credentials": False}}, tools]
        job = {"runs-on": runner, "timeout-minutes": 20, "permissions": {"contents": "read"}, "steps": steps}
        if capacity:
            job.update({"if": "vars.PORTAL_CAPACITY_ENABLED == 'true'", "environment": "portal-capacity",
                        "permissions": {"contents": "read", "id-token": "write"},
                        "env": {key: "${{ vars." + key + " }}" for key in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "PORTAL_SUBSCRIPTION_IDS", "PORTAL_CAPACITY_REGIONS")}})
            steps.extend([
                {"run": "python tools/portal_catalog.py capacity-check"},
                {"uses": "azure/login@a457da9ea143d694b1b9c7c869ebb04ebe844ef5", "with": {"client-id": "${{ vars.AZURE_CLIENT_ID }}", "tenant-id": "${{ vars.AZURE_TENANT_ID }}", "allow-no-subscriptions": True}},
                {"run": "python tools/portal_catalog.py capacity"},
            ])
        else:
            job["if"] = "github.event_name != 'push' || github.ref_name == (vars.PORTAL_CATALOG_BRANCH || github.event.repository.default_branch)"
            steps.extend([
                {"run": "python -m pip install httpx==0.28.1\naz bicep install --version v0.38.33\nterraform version"},
                {"run": "python tools/portal_catalog.py build"},
            ])
        steps.append({"uses": upload, "with": {"name": name, "path": "runs/portal-generated/", "if-no-files-found": "error"}})
        publisher = {"needs": "refresh", "runs-on": runner, "timeout-minutes": 5, "permissions": {"contents": "write"}, "steps": [
            {"uses": checkout, "with": {"ref": branch}},
            {"uses": download, "with": {"name": name, "path": "runs/portal-generated/"}},
            {"run": "python tools/portal_catalog.py publish"},
        ]}
        workflow = {"name": "Refresh subscription capacity" if capacity else "Validate catalog and refresh retail prices", "on": trigger,
                    "permissions": {"contents": "read"}, "concurrency": {"group": "portal-generated-${{ github.ref }}", "cancel-in-progress": False},
                    "jobs": {"refresh": job, "publish": publisher}}
        outputs[f".github/workflows/{name}.yml"] = (json.dumps(workflow, indent=2) + "\n").encode()
    return outputs