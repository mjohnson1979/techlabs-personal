# Contributing

This organization branch is an engineering preview. Review the
[release checklist](docs/RELEASE-CHECKLIST.md) before external distribution.

## Validation

Use Python 3.12, install [runtime dependencies](site/requirements.txt) and pytest
in a virtual environment, then run:

```powershell
python -m pytest site/server/tests
node --check site/assets/app.js
node --check site/assets/setup.js
```

Backend tests use temporary databases, synthetic fixtures and mocked cloud calls.
Do not restore internal catalogs to make tests pass or include test fixtures in a
organization package. Keep organization snapshots empty and the distribution marker set
to `organization`. Test legacy helper behavior only with explicit test configuration.

Use the allowlist exporter in [tools/build_distribution.py](tools/build_distribution.py)
to produce a history-free candidate. Do not commit runtime credentials, generated
archives, organization workload code, deployment records, Terraform state or logs.
Do not add deployment workflows targeting the originating internal environment.