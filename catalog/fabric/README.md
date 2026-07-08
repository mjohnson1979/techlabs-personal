# Microsoft Fabric Capacity

Deploy a Microsoft Fabric capacity using Bicep with environment-aware parameters.

## What this deploys

- Microsoft Fabric capacity (`Microsoft.Fabric/capacities`)
- Configurable SKU (`F2` through `F2048`)
- Configurable admin members list

## Parameters

| Name | Default | Description |
| --- | --- | --- |
| `location` | resource group location | Azure region |
| `environment` | `dev` | Environment label |
| `prefix` | `techlabs` | Resource naming prefix |
| `skuName` | `F2` | Fabric capacity SKU |
| `adminMembers` | `['fabric-admin@contoso.com']` | Fabric admin UPN/email list |

## Deploy

```powershell
az group create --name rg-techlabs-fabric --location eastus

# Dev
az deployment group create `
  --resource-group rg-techlabs-fabric `
  --template-file ./main.bicep `
  --parameters ./parameters/dev.bicepparam

# Prod
az deployment group create `
  --resource-group rg-techlabs-fabric `
  --template-file ./main.bicep `
  --parameters ./parameters/prod.bicepparam
```

## Notes

- Update `adminMembers` in parameter files before deployment.
- Fabric availability and entitlements can vary by tenant/region.

## Clean up

```powershell
az group delete --name rg-techlabs-fabric --yes --no-wait
```
