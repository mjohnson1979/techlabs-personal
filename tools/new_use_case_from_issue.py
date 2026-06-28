#!/usr/bin/env python3
"""Scaffold a catalog use case from a "Submit a use case" GitHub issue.

The intake workflow (.github/workflows/use-case-intake.yml) runs this after a
user files the use-case submission issue form. It parses the rendered issue
body, writes catalog/<slug>/metadata.json (+ README.md + a main.bicep stub),
and reports the slug/title back to the workflow via $GITHUB_OUTPUT so it can
open a draft pull request.

No third-party dependencies — standard library only. The metadata it writes is
intentionally a *draft*: it lists the services and narrative the submitter
provided, but leaves Bicep, parameters, Well-Architected pillars and priced
cost components for a maintainer to complete during PR review.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "catalog"

# Heading label (as rendered in the issue body) -> metadata key.
FIELD_MAP = {
    "use case name": "displayName",
    "short identifier (slug)": "slug",
    "category": "category",
    "one-line description": "description",
    "what it is": "what",
    "why it matters": "why",
    "azure services used": "services",
    "example scenarios": "potential",
    "industries": "industries",
    "compliance baselines": "compliance",
    "your contact (optional)": "contact",
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


def parse_issue_body(body: str) -> dict[str, str]:
    """Split a GitHub issue-form body ("### Label\\n\\nvalue") into a field map."""
    fields: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []

    def flush() -> None:
        if current is None:
            return
        key = FIELD_MAP.get(current.strip().lower())
        if key:
            value = "\n".join(chunks).strip()
            if value.lower() in ("_no response_", "_none_", "none", "n/a"):
                value = ""
            fields[key] = value

    for line in body.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^#{2,4}\s+(.*)$", line)
        if m:
            flush()
            current = m.group(1)
            chunks = []
        elif current is not None:
            chunks.append(line)
    flush()
    return fields


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


def split_lines(value: str) -> list[str]:
    out = []
    for raw in value.replace("\r\n", "\n").split("\n"):
        item = raw.strip().lstrip("-*0123456789. ").strip()
        if item:
            out.append(item)
    return out


def split_csv(value: str, default: list[str]) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,;]", value) if p.strip()]
    return parts or list(default)


def canonical_service(name: str) -> str:
    low = " " + name.lower() + " "
    for aliases, canonical in SERVICE_ALIASES:
        if any(a in low for a in aliases):
            return canonical
    return name.strip()


def build_metadata(fields: dict[str, str], slug: str, author: str, issue: str) -> dict:
    services = split_lines(fields.get("services", ""))
    components = []
    for svc in services:
        canonical = canonical_service(svc)
        components.append(
            {
                "name": f"{svc} (usage)",
                "billing": "usage",
                "serviceName": canonical,
                "unitOfMeasure": "",
                "quantityPerMonth": None,
                "notes": "Estimate pending maintainer review.",
            }
        )

    meta = {
        "name": slug,
        "displayName": fields.get("displayName", slug).strip(),
        "category": fields.get("category", "Other").strip() or "Other",
        "description": fields.get("description", "").strip(),
        "what": fields.get("what", "").strip(),
        "why": fields.get("why", "").strip(),
        "potentialUseCases": split_lines(fields.get("potential", "")),
        "industries": split_csv(fields.get("industries", ""), ["Cross-industry"]),
        "compliance": split_csv(fields.get("compliance", ""), ["Baseline"]),
        "cost": {
            "currency": "USD",
            "region": "eastus",
            "profile": "Community submission - costs pending review",
            "components": components,
        },
        "submission": {
            "source": "gallery-issue-form",
            "issue": int(issue) if str(issue).isdigit() else issue,
            "submittedBy": author,
        },
    }
    return meta


def render_readme(meta: dict, issue: str, author: str) -> str:
    services = ", ".join(
        c["serviceName"] for c in meta["cost"]["components"]
    ) or "_none listed_"
    return f"""# {meta['displayName']}

> Community submission via the gallery use-case form (issue #{issue}, by @{author}).
> This is a **draft** — a maintainer must complete the Infrastructure as Code
> before it can be deployed from the gallery.

{meta['description']}

## What it is

{meta['what']}

## Why it matters

{meta['why']}

## Azure services

{services}

## Maintainer checklist

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
    body = os.environ.get("ISSUE_BODY", "")
    author = os.environ.get("ISSUE_AUTHOR", "unknown")
    issue = os.environ.get("ISSUE_NUMBER", "0")
    if not body.strip():
        print("ISSUE_BODY is empty; nothing to scaffold.", file=sys.stderr)
        return 1

    fields = parse_issue_body(body)
    if not fields.get("displayName") and not fields.get("slug"):
        print("Could not find a use case name or slug in the issue.", file=sys.stderr)
        return 1

    base = fields.get("slug", "").strip() or fields.get("displayName", "")
    slug = unique_slug(slugify(base))
    if not slug:
        print("Could not derive a valid slug.", file=sys.stderr)
        return 1

    meta = build_metadata(fields, slug, author, issue)
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
