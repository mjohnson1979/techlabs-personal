# Azure SRE Agent Operations Demo

This use case deploys Azure SRE Agent and a disposable Azure Container Apps workload for demonstrating autonomous incident remediation, cost optimization, and security posture correction.

For complete setup, rehearsal, verification, troubleshooting, and cleanup instructions, see the [operator guide](OPERATIONS.md).

## What gets deployed

| Resource | Purpose |
| --- | --- |
| Azure SRE Agent | Autonomous investigation and governed remediation |
| User-assigned managed identity | Agent access restricted to this demo resource group |
| Azure Container App | Isolated workload operated by the agent |
| Container Apps environment | Consumption hosting and centralized logging |
| Log Analytics workspace | Container logs and investigation evidence |
| Application Insights | Availability telemetry and agent integration |
| Standard availability test | HTTPS checks from three Azure locations |
| Azure Monitor metric alert | Stable incident trigger named `SRE demo availability failure` |
| Resource-group role assignments | Container Apps management and telemetry read access for the agent |

The healthy baseline uses HTTPS-only ingress and three minimum replicas. It contains no business data or credentials.

## Deploy

The deploying identity needs **Owner**, or **Contributor** plus **User Access Administrator**, on the target resource group. Create or select a Microsoft Entra security group whose members will administer the agent, then retrieve its object ID.

```powershell
$sponsorGroupObjectId = az ad group show `
    --group '<sre-admin-group-name-or-object-id>' `
    --query id `
    --output tsv

az group create --name rg-sre-demo --location eastus2
az deployment group create `
    --resource-group rg-sre-demo `
    --parameters catalog/azure-sre-agent-demo/parameters/dev.bicepparam `
    --parameters sponsorGroupObjectId=$sponsorGroupObjectId
```

The deployment creates the agent in Autonomous mode with High access, assigns the sponsor group **SRE Agent Administrator**, and restricts the agent identity to the demo resource group. The identity receives **Container Apps Contributor**, **Monitoring Reader**, and **Log Analytics Reader** on that resource group.

`Container Apps Contributor` includes delete permission. The runbooks prohibit deletion, but the demo resource group must remain disposable and contain no business data.

## Complete agent setup

1. Open [sre.azure.com](https://sre.azure.com/) and select the deployed agent.
2. Connect this TechLabs repository and upload the files under `runbooks/` as operational knowledge.
3. Confirm the deployed Log Analytics workspace and Application Insights component appear under **Logs**; add them if the portal does not discover them automatically.
4. Connect Azure Monitor under **Incidents > Triggers + response plans**.
5. Create an Autonomous response plan whose title filter contains `SRE demo availability failure`.

Repository credentials, knowledge uploads, and response-plan instructions remain post-deployment because they use data-plane or interactive authorization. Never grant this demonstration subscription-wide privileged access.

## Scenario 1: Auto-remediation

Trigger the incident:

```powershell
.\catalog\azure-sre-agent-demo\scripts\Set-SreDemoScenario.ps1 `
    -Scenario AutoRemediation `
    -SubscriptionId <subscription-id> `
    -ResourceGroupName rg-sre-demo `
    -ContainerAppName <container-app-name>
```

Disabling ingress causes the availability test to fail. The response plan should investigate, follow `runbooks/auto-remediation.md`, restore secure ingress, and verify HTTPS health.

## Scenario 2: Cost optimization

Create a scheduled task or run the following instruction on demand:

> Follow the cost optimization runbook for the Container App in rg-sre-demo. Analyze at least 30 minutes of CPU, memory, requests, replicas, and availability. Include resource-group cost evidence when available. If every runbook condition is met, autonomously reduce only minReplicas from 3 to 1 and verify HTTPS health.

Prepare the baseline with `-Scenario CostOptimization`. Azure Cost Management data can lag, so live demonstrations may use utilization and replica evidence while noting that limitation.

## Scenario 3: Security posture

Enable the deliberate misconfiguration with `-Scenario SecurityPosture`, then run:

> Follow the security posture runbook for the Container App in rg-sre-demo. If insecure HTTP ingress is enabled, autonomously disable only allowInsecure and verify HTTPS remains healthy.

The workload is data-free, but reset it immediately after the demonstration.

## Reset

```powershell
.\catalog\azure-sre-agent-demo\scripts\Set-SreDemoScenario.ps1 `
    -Scenario Reset `
    -SubscriptionId <subscription-id> `
    -ResourceGroupName rg-sre-demo `
    -ContainerAppName <container-app-name>
```

Reset restores external HTTPS ingress, disables insecure HTTP, and returns the replica range to 3-5.

## Cost and cleanup

The catalog estimate covers Container Apps and Log Analytics usage. Azure SRE Agent uses separate Azure Agent Unit billing, including always-on charges after any evaluation waiver; those charges are not included in the estimate.

Delete the complete resource group when finished so the SRE Agent and its supporting resources stop billing:

```powershell
az group delete --name rg-sre-demo --yes --no-wait
```

## References

- [Create and set up Azure SRE Agent](https://learn.microsoft.com/azure/sre-agent/create-and-set-up)
- [Automate incident response](https://learn.microsoft.com/azure/sre-agent/automate-incidents)
- [Execute mitigations](https://learn.microsoft.com/azure/sre-agent/execute-mitigations)
- [Schedule tasks](https://learn.microsoft.com/azure/sre-agent/scheduled-tasks)
- [Pricing and billing](https://learn.microsoft.com/azure/sre-agent/pricing-billing)