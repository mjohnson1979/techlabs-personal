# Control plane

The backend serves the customer portal, authenticates through customer-configured
App Service Authentication, and runs reviewed customer workloads using managed
identity. See [customer setup](../../customer/README.md).

Install dependencies from [site/requirements.txt](../requirements.txt), not a second
server requirements file. Set environment values from [.env.example](.env.example)
through the host; the example is not loaded automatically.

```powershell
python site/server/distribution.py --check
```

Run the command above from the repository root. Actual app startup intentionally
fails without customer configuration. Use the separate read-only preview launcher
for UI review without credentials or cloud operations.