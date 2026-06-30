---
applyTo: '**/*.py'
description: 'Python and FastAPI best practices for the Tech Labs control-plane backend (site/server) and PowerShell-adjacent tooling.'
---

# Python & FastAPI Conventions

These conventions apply to the FastAPI control-plane backend under `site/` (entry point
`site/main.py`, package code in `site/server/`) and any supporting Python tooling.

## General

- Target Python 3.12 (the App Service runtime). Do not use syntax newer than 3.12.
- Follow PEP 8. Keep lines reasonable, prefer 4-space indentation, snake_case for
  functions/variables, PascalCase for classes, UPPER_SNAKE_CASE for module constants.
- Add type hints to all public function signatures and dataclass/model fields.
- Prefer standard library and already-vendored dependencies. Before adding a new
  dependency, confirm it is needed and add it to the deployed `site/requirements.txt`
  (the file Oryx/CI installs from), not only to `site/server/requirements.txt`.
- Pin or lower-bound dependencies in `site/requirements.txt` consistent with the
  existing style (e.g. `fastapi>=0.111`).

## Imports & structure

- Group imports: standard library, third-party, then local (`server.*`) — separated by
  blank lines. No wildcard imports.
- Keep modules focused: routing in `api.py`/`app.py`, auth in `entra_auth.py`/`identity.py`,
  Azure SDK calls in `azure_deploy.py`, persistence in `db.py`. Put new logic in the
  module that matches its responsibility instead of growing `main.py`.
- Avoid import-time side effects. Anything that needs credentials, network, or env vars
  must run inside a function or FastAPI startup handler, never at module top level — a
  missing dependency or env var at import time crashes worker boot.

## FastAPI

- Use `async def` for endpoints that perform I/O; keep CPU-bound work out of the event loop.
- Validate request/response bodies with Pydantic models; never trust raw client input.
- Endpoints that accept `Form`, `File`, or `UploadFile` require `python-multipart` — it
  must be present in `site/requirements.txt` or the app fails to boot (regression seen
  previously). Add a smoke import/test when introducing such routes.
- Return explicit status codes; raise `HTTPException` with a clear `detail` for client errors.
- Validate uploaded archives before extraction: enforce size limits, reject absolute or
  `..` paths (zip-slip), and only accept the expected file set.

## Async & HTTP

- Use `httpx.AsyncClient` for outbound HTTP from async code; reuse a client where practical
  and always set timeouts. Do not use blocking `requests` inside `async def`.
- Use `azure-identity` (`DefaultAzureCredential` / managed identity) for Azure auth — never
  hardcode keys or connection strings.

## Security

- Never log secrets, tokens, credentials, or full request bodies that may contain them.
- Read configuration and secrets from environment variables; do not commit them.
- Parameterize any database/query access; never build queries via string concatenation.
- Sanitize and constrain anything used to build file paths, shell commands, or Azure
  resource names derived from user input.

## Errors & logging

- Catch narrow exception types; let unexpected errors surface rather than swallowing them.
- Use the `logging` module (not `print`) in backend code; log actionable context without secrets.
- Do not add try/except for conditions that cannot occur — validate only at system boundaries.

## Testing

- Tests live in `site/server/tests/` and run with `pytest`. Name files `test_*.py`.
- Cover the happy path and the failure/validation path for new endpoints and helpers.
- Keep tests independent and runnable offline — mock Azure SDK and network calls.
- Run `python -m pytest site/server/tests` (from the repo root, venv activated) before pushing
  changes to the backend.

## Style discipline

- Make only the change requested; do not refactor or reformat unrelated code.
- Do not add docstrings/comments to code you did not change. Comment the "why", not the "what".
