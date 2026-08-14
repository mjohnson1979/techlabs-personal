# Microsoft Purview Account

Deploy a Microsoft Purview account using the AVM-backed module in this use-case wrapper.

## What this deploys

- Microsoft Purview account (`Microsoft.Purview/accounts`)
- Configurable public network access settings
- Environment-tagged deployment outputs for governance

## Parameters

| Name | Default | Description |
| --- | --- | --- |
| `location` | `uksouth` | Azure region (Purview availability varies by region) |
| `environment` | `dev` | Environment label |
| `prefix` | `techlabs` | Resource naming prefix |
| `publicNetworkAccess` | `NotSpecified` | Purview account public network access |
| `managedResourcesPublicNetworkAccess` | `NotSpecified` | Managed resource public network access |

## Deploy

```powershell
az group create --name rg-techlabs-purview --location uksouth

# Dev
az deployment group create `
  --resource-group rg-techlabs-purview `
  --template-file ./main.bicep `
  --parameters ./parameters/dev.bicepparam

# Prod
az deployment group create `
  --resource-group rg-techlabs-purview `
  --template-file ./main.bicep `
  --parameters ./parameters/prod.bicepparam
```

## Notes

- Purview availability is region-dependent; `uksouth` is a conservative default.
- Adjust network settings according to your security requirements.
