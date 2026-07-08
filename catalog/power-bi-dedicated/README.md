# Power BI Dedicated Capacity

Deploy a Power BI Dedicated capacity using the AVM-backed module in this use-case wrapper.

## What this deploys

- Power BI Dedicated capacity (`Microsoft.PowerBIDedicated/capacities`)
- Configurable SKU (`A1` through `A6`)
- Configurable admin member object IDs

## Parameters

| Name | Default | Description |
| --- | --- | --- |
| `location` | resource group location | Azure region |
| `environment` | `dev` | Environment label |
| `prefix` | `techlabs` | Resource naming prefix |
| `members` | `['00000000-0000-0000-0000-000000000000']` | Capacity admin member object IDs |
| `skuName` | `A1` | Capacity SKU name |
| `skuCapacity` | `1` | Capacity units |

## Deploy

```powershell
az group create --name rg-techlabs-powerbi --location eastus

# Dev
az deployment group create `
  --resource-group rg-techlabs-powerbi `
  --template-file ./main.bicep `
  --parameters ./parameters/dev.bicepparam

# Prod
az deployment group create `
  --resource-group rg-techlabs-powerbi `
  --template-file ./main.bicep `
  --parameters ./parameters/prod.bicepparam
```

## Notes

- Update `members` with valid Entra object IDs before deployment.
- Verify regional availability for the selected SKU.
