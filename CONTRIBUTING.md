# Contributing

Azure Tech Labs is a catalog of templatized Azure use cases (Infrastructure as
Code) for spinning up demos and MVPs quickly. Contributions that add new use
cases, improve existing ones, or strengthen the shared modules are welcome.

## Use-case contract

Every use case under `catalog/<use-case>/` must include:

- `main.bicep` — the deployable template (resource-group scoped by default).
- `parameters/<env>.bicepparam` — at least a `dev` parameter file.
- `README.md` — what it deploys, its parameters, deploy command, and clean-up.

### Conventions

- **Sensible defaults.** A demo should deploy with no parameter edits required.
- **Reuse modules.** Prefer the shared building blocks in `modules/` over
  re-declaring common resources. Add new shared resources there.
- **Secure by default.** HTTPS-only, TLS 1.2 minimum, RBAC over access policies,
  no public blob access unless the scenario requires it.
- **Tag resources.** Include at least `Environment` and `Owner` tags.
- **Name with a prefix.** Use the `prefix` + `environment` pattern for names and
  `uniqueString(resourceGroup().id)` where global uniqueness is required.

## Validate before opening a PR

```powershell
az bicep build --file catalog/<use-case>/main.bicep --stdout
az bicep build-params --file catalog/<use-case>/parameters/dev.bicepparam --stdout
```

CI runs the same build/lint across all templates, modules, and parameter files.

## Contributor License Agreement

This project welcomes contributions and suggestions. Most contributions require
you to agree to a Contributor License Agreement (CLA) declaring that you have the
right to, and actually do, grant us the rights to use your contribution. For
details, visit [https://cla.opensource.microsoft.com](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether
you need to provide a CLA and decorate the PR appropriately. Follow the
instructions provided by the bot. You will only need to do this once across all
repos using our CLA.

This project has adopted the
[Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the
[Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any
additional questions or comments.
