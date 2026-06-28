#!/usr/bin/env python3
"""Scaffold a catalog use case from an AI-structured submission.

The intake workflow (.github/workflows/use-case-intake.yml) first asks GitHub
Models (via actions/ai-inference) to turn the submitter's natural-language
description into a small JSON object, then runs this script to validate it and
write catalog/<slug>/metadata.json (+ README.md + a main.bicep stub). It reports
the slug/title back to the workflow via $GITHUB_OUTPUT so it can open a draft PR.

Input - the AI-extracted JSON - is read from the file named by EXTRACTED_JSON_FILE
(or the EXTRACTED_JSON env var). Expected keys: displayName, slug, category,
description, what, why, potentialUseCases[], services[], industries[], compliance[].

No third-party dependencies - standard library only. The metadata it writes is a
*draft*: it captures the narrative and the services the AI identified, but leaves
Bicep, parameters, Well-Architected pillars and priced cost components for a
maintainer to complete during PR review.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "catalog"

ALLOWED_CATEGORIES = {
    "AI / GenAI", "Web & API", "Data & Analytics", "Containers", "Networking",
    "Integration", "End-User Computing", "Hybrid & Migration", "Foundation",
    "Security & Identity", "Other",
}

# Free-text Azure service -> canonical cost-meter serviceName understood by
# site/assets/azure-icons.js (so the gallery picks the right icon/tier). The
# first alias substring that matches wins; unmatched services keep their raw
# name (and fall back to a generic icon).
SERVICE_ALIASES = [
    (("azure openai", "openai", "gpt", "cognitive services"), "Azure OpenAI"),
    (("ai search", "cognitive search", "ai studio search"), "Azure Cognitive Search"),
    (("cosmos",), "Azure Cosmos DB"),
    (("sql database", "azure sql", " sql"), "SQL Database"),
    (("app service", "appservice", "web app", "webapp"), "Azure App Service"),
    (("container apps", "container app", " aca"), "Azure Container Apps"),
    (("kubernetes", "aks"), "Azure Kubernetes Service"),
    (("container registry", "acr"), "Container Registry"),
    (("functions", "function app", "azure functions"), "Functions"),
    (("logic apps", "logic app"), "Logic Apps"),
    (("api management", "apim"), "API Management"),
    (("application gateway", "app gateway"), "Application Gateway"),
    (("front door",), "Azure Front Door Service"),
    (("firewall",), "Azure Firewall"),
    (("bastion",), "Azure Bastion"),
    (("virtual network", "vnet"), "Virtual Network"),
    (("expressroute", "express route"), "expressroute"),
    (("service bus", "servicebus"), "Service Bus"),
    (("event hubs", "event hub", "eventhub"), "Event Hubs"),
    (("stream analytics",), "Stream Analytics"),
    (("data factory", "adf"), "Azure Data Factory v2"),
    (("databricks",), "Azure Databricks"),
    (("key vault", "keyvault"), "Key Vault"),
    (("log analytics",), "Log Analytics"),
    (("application insights", "app insights", "monitor"), "Azure Monitor"),
    (("storage", "blob", "data lake"), "Storage"),
    (("virtual machine", "virtual machines", " vm", "vmss"), "Virtual Machines"),
]


def load_extracted() -> dict:
    """Read and parse the AI-extracted JSON (tolerating ```json code fences)."""
    raw = os.environ.get("EXTRACTED_JSON", "")
    path = os.environ.get("EXTRACTED_JSON_FILE", "")
    if not raw and path:
        raw = Path(path).read_text(encoding="utf-8")
    raw = raw.strip()
    if not raw:
        raise ValueError("No AI-extracted JSON provided (set EXTRACTED_JSON[_FILE]).")
    # Strip a leading/trailing markdown code fence if the model added one.
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fall back to the first {...} block if there's stray prose around it.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def unique_slug(slug: str) -> str:
    """Avoid clobbering an existing catalog entry by suffixing -2, -3, ..."""
    if not (CATALOG_DIR / slug).exists():
        return slug
    i = 2
    while (CATALOG_DIR / f"{slug}-{i}").exists():
        i += 1
    return f"{slug}-{i}"


def as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [p.strip() for p in re.split(r"[\n,;]", value) if p.strip()]
    return []


def canonical_service(name: str) -> str:
    low = " " + name.lower() + " "
    for aliases, canonical in SERVICE_ALIASES:
        if any(a in low for a in aliases):
            return canonical
    return name.strip()


def build_metadata(data: dict, slug: str, author: str, issue: str, original: str) -> dict:
    services = as_list(data.get("services"))
    components = []
    for svc in services:
        components.append(
            {
                "name": f"{svc} (usage)",
                "billing": "usage",
                "serviceName": canonical_service(svc),
                "unitOfMeasure": "",
                "quantityPerMonth": None,
                "notes": "Estimate pending maintainer review.",
            }
        )

    category = str(data.get("category", "")).strip()
    if category not in ALLOWED_CATEGORIES:
        category = "Other"

    industries = as_list(data.get("industries")) or ["Cross-industry"]
    compliance = as_list(data.get("compliance")) or ["Baseline"]

    return {
        "name": slug,
        "displayName": str(data.get("displayName", slug)).strip() or slug,
        "category": category,
        "description": str(data.get("description", "")).strip(),
        "what": str(data.get("what", "")).strip(),
        "why": str(data.get("why", "")).strip(),
        "potentialUseCases": as_list(data.get("potentialUseCases")),
        "industries": industries,
        "compliance": compliance,
        "cost": {
            "currency": "USD",
            "region": "eastus",
            "profile": "Community submission - costs pending review",
            "components": components,
        },
        "submission": {
            "source": "gallery-ai-intake",
            "issue": int(issue) if str(issue).isdigit() else issue,
            "submittedBy": author,
            "originalDescription": original.strip(),
        },
    }


def render_readme(meta: dict, issue: str, author: str) -> str:
    services = ", ".join(
        c["serviceName"] for c in meta["cost"]["components"]
    ) or "_none listed_"
    return f"""# {meta['displayName']}

> Community submission, AI-structured from the gallery use-case form
> (issue #{issue}, by @{author}). This is a **draft** - a maintainer must complete
> the Infrastructure as Code before it can be deployed from the gallery.

{meta['description']}

## What it is

{meta['what']}

## Why it matters

{meta['why']}

## Azure services

{services}

## Maintainer checklist

- [ ] Confirm the AI-structured `metadata.json` matches the submitter's intent.
- [ ] Author `main.bicep` for this scenario.
- [ ] Add `parameters/dev.bicepparam` (and `parameters/prod.bicepparam` if applicable).
- [ ] Fill in `wellArchitected` pillars in `metadata.json`.
- [ ] Add `quota` requirements for capacity-sensitive services.
- [ ] Replace the placeholder `cost.components` with priced meters and quantities.
- [ ] Run `./tools/Build-Catalog.ps1` and `./tools/Build-Templates.ps1` to verify.
"""


BICEP_STUB = """// Draft scaffold for a community-submitted use case.
// TODO(maintainer): implement the architecture described in metadata.json and
// add matching parameters/dev.bicepparam (and prod.bicepparam if applicable).
// This file is a placeholder so the catalog entry resolves; it provisions
// nothing yet and is intentionally not wired into parameters until completed.

targetScope = 'resourceGroup'
"""


def write_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def main() -> int:
    author = os.environ.get("ISSUE_AUTHOR", "unknown")
    issue = os.environ.get("ISSUE_NUMBER", "0")
    original = os.environ.get("ORIGINAL_DESCRIPTION", "")

    try:
        data = load_extracted()
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"Could not load AI-extracted metadata: {exc}", file=sys.stderr)
        return 1

    base = str(data.get("slug", "")).strip() or str(data.get("displayName", "")).strip()
    if not base:
        print("AI output had no slug or displayName; cannot scaffold.", file=sys.stderr)
        return 1

    slug = unique_slug(slugify(base))
    if not slug:
        print("Could not derive a valid slug.", file=sys.stderr)
        return 1

    meta = build_metadata(data, slug, author, issue, original)
    dest = CATALOG_DIR / slug
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (dest / "README.md").write_text(
        render_readme(meta, issue, author), encoding="utf-8"
    )
    (dest / "main.bicep").write_text(BICEP_STUB, encoding="utf-8")

    print(f"Scaffolded catalog/{slug} from issue #{issue}")
    write_output("slug", slug)
    write_output("display_name", meta["displayName"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
