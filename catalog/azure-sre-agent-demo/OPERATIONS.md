# Azure SRE Agent Demo Operator Guide

This guide walks an operator through discovery, validation, Azure SRE Agent setup, all three demonstration scenarios, recovery, troubleshooting, and cleanup for the TechLabs Azure SRE Agent Operations Demo.

> This environment is intentionally disposable. The SRE Agent can write to resources in the demo resource group. Do not place business data, credentials, or unrelated resources in that group.

## Guide at a glance

| Phase | Typical duration | Result |
| --- | ---: | --- |
| Discover and validate | 5-10 minutes | The deployed resources and healthy baseline are confirmed |
| Complete SRE Agent setup | 10-20 minutes | Knowledge, logs, Azure Monitor, and a response plan are connected |
| Auto-remediation | 10-20 minutes | The agent restores disabled Container App ingress |
| Cost optimization | 10-15 minutes after 30 minutes of telemetry | The agent reduces minimum replicas from 3 to 1 |
| Security posture | 5-10 minutes | The agent disables insecure HTTP ingress |
| Cleanup | 5-10 minutes | The disposable resource group and billing resources are removed |

Portal labels can change while Azure SRE Agent evolves. When a label differs, use the described intent and expected state to identify the equivalent control.

## 1. Prerequisites

Before starting, confirm that you have:

- Azure CLI installed and authenticated.
- Access to the subscription containing the successful TechLabs deployment.
- Permission to read the demo resource group and operate its Container App.
- Membership in the Microsoft Entra group assigned **SRE Agent Administrator** on the agent. The TechLabs environment uses `TechLabs-SRE-Admins` by default.
- Access to [Azure SRE Agent](https://sre.azure.com/).
- A local clone of this repository with the `az` CLI and the Container Apps extension available.
- Windows PowerShell 5.1 or later.

Confirm the CLI session:

```powershell
az account show --query '{subscription:name,subscriptionId:id,tenantId:tenantId,user:user.name}' --output table
az extension add --name containerapp --upgrade --only-show-errors
```

Expected result: the account command displays the intended subscription and tenant, and the extension command completes without error.

## 2. Discover the successful deployment

Run this block from the repository root. It discovers a resource group tagged by TechLabs and then resolves every resource used in the guide.

```powershell
$subscriptionId = az account show --query id --output tsv
if (-not $subscriptionId) {
    throw 'Azure CLI is not signed in.'
}

$allResourceGroups = az group list `
    --subscription $subscriptionId `
    --output json | ConvertFrom-Json

$resourceGroups = @(
    $allResourceGroups |
        Where-Object { $_.tags.'techlabs-usecase' -eq 'azure-sre-agent-demo' } |
        ForEach-Object { $_.name }
)

if ($resourceGroups.Count -eq 0) {
    throw 'No Azure SRE Agent demo resource group was found in this subscription.'
}

$resourceGroups | ForEach-Object { Write-Host $_ }
$resourceGroupName = $resourceGroups[-1]
Write-Host "Using resource group: $resourceGroupName"

function Get-SingleResourceName {
    param(
        [Parameter(Mandatory)]
        [string]$ResourceType
    )

    $names = @(
        az resource list `
            --subscription $subscriptionId `
            --resource-group $resourceGroupName `
            --resource-type $ResourceType `
            --query '[].name' `
            --output tsv
    )
    if ($names.Count -ne 1) {
        throw "Expected exactly one $ResourceType resource in $resourceGroupName; found $($names.Count)."
    }
    return $names[0]
}

$containerAppName = Get-SingleResourceName 'Microsoft.App/containerApps'
$sreAgentName = Get-SingleResourceName 'Microsoft.App/agents'
$logAnalyticsName = Get-SingleResourceName 'Microsoft.OperationalInsights/workspaces'
$appInsightsName = Get-SingleResourceName 'Microsoft.Insights/components'
$availabilityTestName = Get-SingleResourceName 'Microsoft.Insights/webtests'
$alertName = 'SRE demo availability failure'

$containerApp = az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --output json | ConvertFrom-Json

$containerAppFqdn = $containerApp.properties.configuration.ingress.fqdn
$workspaceCustomerId = az monitor log-analytics workspace show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --workspace-name $logAnalyticsName `
    --query customerId `
    --output tsv

[PSCustomObject]@{
    SubscriptionId       = $subscriptionId
    ResourceGroup        = $resourceGroupName
    SreAgent             = $sreAgentName
    ContainerApp         = $containerAppName
    ContainerAppFqdn     = $containerAppFqdn
    LogAnalytics         = $logAnalyticsName
    WorkspaceCustomerId  = $workspaceCustomerId
    ApplicationInsights  = $appInsightsName
    AvailabilityTest     = $availabilityTestName
    Alert                 = $alertName
} | Format-List
```

If multiple demo resource groups exist, replace `$resourceGroupName` with the successful group you intend to test, then rerun the resource-resolution portion of the block.

## 3. Understand the security boundary

The deployment assigns the SRE Agent's user-assigned managed identity these roles at the demo resource-group scope only:

| Role | Purpose |
| --- | --- |
| Container Apps Contributor | Inspect and update the demo Container App |
| Monitoring Reader | Read alert state and monitoring evidence |
| Log Analytics Reader | Query investigation evidence |

The `TechLabs-SRE-Admins` group receives **SRE Agent Administrator** on the SRE Agent resource.

The optional Entra Agent ID `agentIdentity` payload is intentionally omitted. Tenant `2929f0bd-f87b-47ae-bb73-ec22b65de557` is not allow-listed for that optional feature. This does not prevent the deployed SRE Agent from using its managed identities and scoped Azure RBAC.

`Container Apps Contributor` includes delete permission. The runbooks prohibit deletion, RBAC changes, Key Vault access, subscription-wide writes, and changes outside the connected demo resource group.

## 4. Verify the healthy baseline

Do not trigger a scenario until every baseline check passes.

### 4.1 Verify provisioning states

```powershell
az resource list `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --query '[].{name:name,type:type,state:properties.provisioningState}' `
    --output table
```

Expected result: the workspace, Application Insights component, managed identity, SRE Agent, Container Apps environment, Container App, web test, and metric alert exist. Resources that expose a provisioning state should report `Succeeded`.

### 4.2 Verify Container App configuration

```powershell
$baseline = az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query '{fqdn:properties.configuration.ingress.fqdn,external:properties.configuration.ingress.external,allowInsecure:properties.configuration.ingress.allowInsecure,targetPort:properties.configuration.ingress.targetPort,minReplicas:properties.template.scale.minReplicas,maxReplicas:properties.template.scale.maxReplicas}' `
    --output json | ConvertFrom-Json

$baseline | Format-List
```

Expected values:

- `Fqdn`: populated
- `External`: `True`
- `AllowInsecure`: `False`
- `TargetPort`: `80`
- `MinReplicas`: `3`
- `MaxReplicas`: `5`

### 4.3 Verify HTTPS health

```powershell
$response = Invoke-WebRequest -Uri "https://$($baseline.fqdn)/" -TimeoutSec 30
if ($response.StatusCode -ne 200) {
    throw "Expected HTTP 200; received $($response.StatusCode)."
}
Write-Host "HTTPS baseline passed: HTTP $($response.StatusCode)"
```

### 4.4 Verify the availability test and alert

```powershell
az resource show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --resource-type Microsoft.Insights/webtests `
    --name $availabilityTestName `
    --query '{enabled:properties.Enabled,frequency:properties.Frequency,locations:properties.Locations[].Id}' `
    --output json

az monitor metrics alert show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $alertName `
    --query '{enabled:enabled,severity:severity,evaluationFrequency:evaluationFrequency,windowSize:windowSize}' `
    --output json
```

Expected result: the availability test is enabled with a five-minute frequency and three locations. The alert is enabled, severity 2, evaluated every minute over a five-minute window.

### 4.5 Verify agent RBAC

```powershell
$agentIdentityId = az identity show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name (Get-SingleResourceName 'Microsoft.ManagedIdentity/userAssignedIdentities') `
    --query principalId `
    --output tsv

az role assignment list `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --assignee-object-id $agentIdentityId `
    --query '[].{role:roleDefinitionName,scope:scope}' `
    --output table
```

Expected result: `Container Apps Contributor`, `Monitoring Reader`, and `Log Analytics Reader`, each scoped to the demo resource group.

## 5. Complete SRE Agent portal setup

Infrastructure deployment creates the agent and Azure resources. Repository authorization, knowledge ingestion, log-source confirmation, and response-plan creation remain interactive setup steps.

### 5.1 Open the deployed agent

1. Open [Azure SRE Agent](https://sre.azure.com/).
2. Sign in with an account that belongs to `TechLabs-SRE-Admins`.
3. Select the agent whose name matches `$sreAgentName`.
4. Confirm its resource group matches `$resourceGroupName`.
5. Confirm its operational mode is **Autonomous** and access level is **High**.

For a first rehearsal, you may temporarily choose a supervised or approval-based mode if the portal offers one. Return to Autonomous only after reviewing the runbook scope and the resource-group boundary.

### 5.2 Connect operational knowledge

Depending on the current preview UI, this area may be named **Knowledge**, **Repositories**, or **Knowledge sources**.

1. Select the option to connect a repository or add knowledge.
2. Authorize the TechLabs GitHub repository or your fork.
3. Select the branch containing this demo.
4. Limit the path to `catalog/azure-sre-agent-demo/runbooks/` when path filtering is available.
5. Sync or upload these three files:
   - `auto-remediation.md`
   - `cost-optimization.md`
   - `security-posture.md`
6. Wait until ingestion or indexing reports ready.
7. Open each item and confirm its **Scope**, **Allowed action**, **Verification**, and **Stop conditions** are visible.

Expected result: all three runbooks are available to agent investigations. Repository synchronization can take several minutes.

### 5.3 Confirm telemetry sources

1. Open the agent's **Logs**, **Data sources**, or equivalent page.
2. Confirm Application Insights `$appInsightsName` is connected or ready.
3. Confirm Log Analytics workspace `$logAnalyticsName` is connected or ready.
4. If either is missing, add it by selecting the resource from `$resourceGroupName`.
5. Wait for each source to report connected, ready, or an equivalent healthy state.

Use these commands when the portal requires full resource IDs:

```powershell
$appInsightsResourceId = az resource show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --resource-type Microsoft.Insights/components `
    --name $appInsightsName `
    --query id `
    --output tsv

$logAnalyticsResourceId = az monitor log-analytics workspace show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --workspace-name $logAnalyticsName `
    --query id `
    --output tsv
```

### 5.4 Connect Azure Monitor

1. Open **Incidents**, then **Triggers + response plans**, or the equivalent integration area.
2. Add or connect Azure Monitor.
3. Select `$subscriptionId` and `$resourceGroupName`.
4. Confirm the metric alert named exactly `SRE demo availability failure` is visible.
5. Save the connection and wait for a ready state.

### 5.5 Create the auto-remediation response plan

1. Create a response plan.
2. Name it `SRE Demo Availability Fix`.
3. Choose Azure Monitor alerts as the trigger.
4. Add a title or alert-name filter that contains exactly `SRE demo availability failure`.
5. Associate `auto-remediation.md`, or use this instruction:

   > Follow the auto-remediation runbook. Investigate the Azure Monitor alert named `SRE demo availability failure`, act only on the Container App in the connected demo resource group, and perform only the remediation allowed by the runbook.

6. Select **Autonomous** execution for the end-to-end demo.
7. Save and enable the plan.
8. Confirm the plan reports active or enabled.

Cost optimization and security posture are initiated manually in this guide. Do not attach them to the availability alert.

## 6. Reusable scenario command setup

From the repository root, set the script path once:

```powershell
$scenarioScript = '.\catalog\azure-sre-agent-demo\scripts\Set-SreDemoScenario.ps1'
```

Preview each operation before changing Azure:

```powershell
& $scenarioScript `
    -Scenario Reset `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName `
    -WhatIf
```

The script validates the resolved resource ID before making a change. It will stop if the subscription, resource group, or Container App does not match the requested target.

## 7. Scenario 1: Autonomous auto-remediation

### Objective

Disable external ingress, let the availability test and alert detect the outage, and verify the SRE Agent restores secure ingress autonomously.

### 7.1 Precheck

1. Complete the healthy baseline checks in section 4.
2. Confirm the response plan is active.
3. Confirm no unrelated incident is active for this demo.
4. Run a preview:

```powershell
& $scenarioScript `
    -Scenario AutoRemediation `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName `
    -WhatIf
```

Expected result: PowerShell reports that it would apply the `AutoRemediation` scenario to the exact Container App resource ID.

### 7.2 Trigger the outage

```powershell
& $scenarioScript `
    -Scenario AutoRemediation `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName
```

Expected immediate state:

- `External`: false or absent
- `Fqdn`: empty or no longer reachable
- No resources are deleted

### 7.3 Observe detection and investigation

The synthetic test runs every five minutes, so the complete flow is not immediate.

| Approximate time | Expected event |
| --- | --- |
| T+0 | Ingress is disabled |
| T+0-5 minutes | Availability locations begin reporting failures |
| T+5-10 minutes | The metric alert fires after at least two locations fail |
| T+5-15 minutes | The response plan starts an SRE Agent investigation |
| T+10-20 minutes | The agent restores ingress and verifies health |

In the SRE Agent incident, confirm that the agent:

1. Correlates the exact Azure Monitor alert.
2. Identifies failed availability locations.
3. Inspects the Container App provisioning state, ingress, revisions, replicas, and recent activity.
4. Confirms ingress is disabled.
5. Proposes or performs only this action: enable external ingress on target port 80, transport `auto`, with `allowInsecure=false`.

The agent must stop without changing anything if the target is outside the demo group, ingress is already healthy, the fault is unrelated to ingress, or remediation would require deletion, RBAC changes, Key Vault access, or subscription-wide writes.

### 7.4 Verify remediation

```powershell
$recovered = az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query '{fqdn:properties.configuration.ingress.fqdn,external:properties.configuration.ingress.external,allowInsecure:properties.configuration.ingress.allowInsecure,targetPort:properties.configuration.ingress.targetPort}' `
    --output json | ConvertFrom-Json

$recovered | Format-List
$response = Invoke-WebRequest -Uri "https://$($recovered.fqdn)/" -TimeoutSec 30
Write-Host "HTTPS status: $($response.StatusCode)"
```

Pass criteria:

- `External` is `True`.
- `AllowInsecure` is `False`.
- `TargetPort` is `80`.
- FQDN is populated.
- HTTPS returns HTTP 200.
- The incident records the action and moves toward resolved.
- The availability alert begins resolving after subsequent successful probes.

If the agent does not recover the app, run the reset in section 10 before troubleshooting the response plan.

## 8. Scenario 2: Cost optimization

### Objective

Use at least 30 minutes of healthy utilization evidence to justify reducing only `minReplicas` from 3 to 1.

### 8.1 Prepare the overprovisioned baseline

```powershell
& $scenarioScript `
    -Scenario CostOptimization `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName
```

Verify the starting state:

```powershell
az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query 'properties.template.scale.{minReplicas:minReplicas,maxReplicas:maxReplicas}' `
    --output json
```

Expected result: minimum replicas is 3 and maximum replicas is 5.

Leave the healthy workload running for at least 30 minutes. Avoid generating load during this period.

### 8.2 Start the agent investigation

Open the agent's chat, ad hoc task, or manual investigation experience and submit:

> Follow the cost optimization runbook for the Container App in the connected demo resource group. Analyze at least 30 minutes of CPU, memory, requests, replicas, and availability. Include resource-group cost evidence when available. If every runbook condition is met, autonomously reduce only `minReplicas` from 3 to 1 and verify HTTPS health.

The agent may proceed only when:

- At least 30 minutes of evidence is available.
- Average CPU is below 10 percent.
- Average memory is below 30 percent.
- Request volume is low.
- Availability is healthy.
- Current `minReplicas` is exactly 3.

Azure Cost Management ingestion can lag. The runbook permits the agent to continue using utilization and availability evidence when cost data is not yet available, provided it records that limitation.

### 8.3 Review the proposed action

The only permitted change is:

```text
minReplicas: 3 -> 1
```

The agent must not change CPU, memory, maximum replicas, image, ingress, identity, or any other resource.

### 8.4 Verify optimization

```powershell
$optimized = az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query '{fqdn:properties.configuration.ingress.fqdn,minReplicas:properties.template.scale.minReplicas,maxReplicas:properties.template.scale.maxReplicas,allowInsecure:properties.configuration.ingress.allowInsecure}' `
    --output json | ConvertFrom-Json

$optimized | Format-List
$response = Invoke-WebRequest -Uri "https://$($optimized.fqdn)/" -TimeoutSec 30
Write-Host "HTTPS status: $($response.StatusCode)"
```

Pass criteria:

- `MinReplicas` is `1`.
- `MaxReplicas` remains `5`.
- `AllowInsecure` remains `False`.
- HTTPS returns HTTP 200.
- No new availability incident appears.
- The investigation records the utilization evidence and notes whether cost data was available.

Run the reset in section 10 before the next scenario.

## 9. Scenario 3: Security posture correction

### Objective

Enable insecure HTTP deliberately, then verify the SRE Agent changes only `allowInsecure` back to false.

### 9.1 Precheck and preview

Reset the demo first, then preview the change:

```powershell
& $scenarioScript `
    -Scenario Reset `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName

& $scenarioScript `
    -Scenario SecurityPosture `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName `
    -WhatIf
```

### 9.2 Introduce the misconfiguration

```powershell
& $scenarioScript `
    -Scenario SecurityPosture `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName
```

Expected result: `AllowInsecure` is `True`; HTTPS remains healthy.

Confirm the state:

```powershell
az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query 'properties.configuration.ingress.{fqdn:fqdn,external:external,allowInsecure:allowInsecure}' `
    --output json
```

### 9.3 Start the agent investigation

Open the agent's chat, ad hoc task, or manual investigation experience and submit:

> Follow the security posture runbook for the Container App in the connected demo resource group. If insecure HTTP ingress is enabled, autonomously disable only `allowInsecure` and verify HTTPS remains healthy.

The agent must inspect the ingress configuration, confirm HTTPS is healthy, and change only `allowInsecure` from true to false. It must stop if the app is already HTTPS-only, the target is outside the demo group, or remediation requires deletion, RBAC changes, Key Vault access, identity changes, or subscription-wide writes.

### 9.4 Verify correction

```powershell
$secured = az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query 'properties.configuration.ingress.{fqdn:fqdn,external:external,allowInsecure:allowInsecure}' `
    --output json | ConvertFrom-Json

$secured | Format-List
$httpsResponse = Invoke-WebRequest -Uri "https://$($secured.fqdn)/" -TimeoutSec 30
Write-Host "HTTPS status: $($httpsResponse.StatusCode)"

try {
    $httpResponse = Invoke-WebRequest `
        -Uri "http://$($secured.fqdn)/" `
        -MaximumRedirection 0 `
        -TimeoutSec 30 `
        -ErrorAction Stop
    Write-Host "HTTP status: $($httpResponse.StatusCode)"
} catch {
    Write-Host "Plaintext HTTP was rejected or redirected: $($_.Exception.Message)"
}
```

Pass criteria:

- `AllowInsecure` is `False`.
- `External` remains `True`.
- HTTPS returns HTTP 200.
- Plaintext HTTP is rejected or redirected to HTTPS.
- No unrelated property or resource changed.

## 10. Reset between scenarios

Run reset after every scenario and whenever a scenario does not complete as expected:

```powershell
& $scenarioScript `
    -Scenario Reset `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName
```

Reset restores:

- External ingress enabled
- Target port 80
- Transport `auto`
- `allowInsecure=false`
- Minimum replicas 3
- Maximum replicas 5

Verify:

```powershell
$resetState = az containerapp show `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $containerAppName `
    --query '{fqdn:properties.configuration.ingress.fqdn,external:properties.configuration.ingress.external,allowInsecure:properties.configuration.ingress.allowInsecure,targetPort:properties.configuration.ingress.targetPort,minReplicas:properties.template.scale.minReplicas,maxReplicas:properties.template.scale.maxReplicas}' `
    --output json | ConvertFrom-Json

$resetState | Format-List
Invoke-WebRequest -Uri "https://$($resetState.fqdn)/" -TimeoutSec 30 | Select-Object StatusCode
```

Wait for successful availability probes and alert resolution before triggering the next incident.

## 11. Troubleshooting

### Deployment or generated artifact problems

| Symptom | Cause | Resolution |
| --- | --- | --- |
| `Unexpected UTF-8 BOM` | A stale server used strict UTF-8 to read PowerShell-generated ARM JSON | Update to the current code, regenerate templates, and restart the server. The loader now uses `utf-8-sig`. |
| `AppId and ConnectionString fields must be provided together` | A stale template supplied only the Application Insights connection string | Regenerate templates from current source. The agent payload now supplies both fields. |
| Tenant is not allowed to create agent identities | A stale template included optional Entra `agentIdentity` | Regenerate from current source. The unsupported property is intentionally omitted. |
| Log Analytics workspace `ResourceNotFound` during deployment | A stale template used a nested workspace deployment and parent-level `listKeys` | Regenerate from current source. The workspace is now inline with a direct dependency. |
| Source is fixed but deployment repeats an old error | The local FastAPI process or generated ARM files are stale | Run `tools/Build-Templates.ps1`, confirm the SRE dev/prod files are rebuilt, then restart FastAPI. |

The repository-wide template build can stop later on an unrelated Azure VMware Solution Bicep warning after the SRE files have already been generated. Confirm that `site/server/_templates/azure-sre-agent-demo/dev.json` and `prod.json` were built before treating that warning as an SRE failure.

### Portal and scenario problems

| Symptom | Check | Resolution |
| --- | --- | --- |
| Runbooks are unavailable | Repository sync or knowledge indexing state | Resync the `runbooks/` path and wait for all three files to report ready. |
| Logs cannot be queried | Application Insights and Log Analytics connection states | Reconnect the resources from the demo group and allow 5-15 minutes for initial ingestion. |
| Alert does not fire | Availability test failures, five-minute frequency, two-location threshold | Wait at least two probe/evaluation cycles and confirm ingress is actually disabled. |
| Alert fires but no investigation starts | Azure Monitor connection, response-plan status, exact title filter | Enable the plan and use the exact alert name `SRE demo availability failure`. |
| Investigation proposes a change but does not execute | Plan mode or approval setting | For this disposable demo, select Autonomous after confirming scope. Otherwise approve the supervised action manually. |
| Agent reports insufficient permission | Managed identity role assignments and resource-group scope | Confirm the three roles in section 4.5. Do not add subscription-wide access. |
| Cost runbook stops without changes | Less than 30 minutes of data, threshold exceeded, unhealthy availability, or minimum not equal to 3 | Reset, wait for 30 healthy idle minutes, then rerun the instruction. |
| Scenario script reports a resource-ID mismatch | Wrong subscription, group, or Container App name | Rerun section 2 and use the discovered values. |
| Partial resources remain after a failed deployment | ARM created resources before a later resource failed | Use the TechLabs **Clean up** action or delete only that disposable failed resource group. |

### Inspect a failed ARM deployment

```powershell
$failedDeployment = az deployment group list `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --query "[?properties.provisioningState=='Failed'] | [-1].name" `
    --output tsv

az deployment operation group list `
    --subscription $subscriptionId `
    --resource-group $resourceGroupName `
    --name $failedDeployment `
    --query "[?properties.provisioningState=='Failed'].{resource:properties.targetResource.resourceName,type:properties.targetResource.resourceType,status:properties.statusCode,error:properties.statusMessage.error}" `
    --output json
```

## 12. Cleanup

Reset first if you want a final healthy-state record:

```powershell
& $scenarioScript `
    -Scenario Reset `
    -SubscriptionId $subscriptionId `
    -ResourceGroupName $resourceGroupName `
    -ContainerAppName $containerAppName
```

Then remove the demo through the TechLabs **Clean up** action, or delete the disposable resource group directly:

```powershell
az group delete `
    --subscription $subscriptionId `
    --name $resourceGroupName `
    --yes `
    --no-wait
```

Verify deletion:

```powershell
az group exists `
    --subscription $subscriptionId `
    --name $resourceGroupName
```

Expected result after deletion completes: `false`.

Resource-group deletion removes the SRE Agent and supporting Azure resources and stops their associated billing. Response plans or portal connections that survive resource deletion should be removed from the SRE Agent portal.

The tenant-level `TechLabs-SRE-Admins` group is outside the resource-group cleanup boundary. Keep it for future demos, or ask a tenant administrator to remove it after confirming no other agent uses it.

## 13. End-to-end completion checklist

- [ ] Azure CLI points to the correct subscription and tenant.
- [ ] Exactly one intended demo resource group is selected.
- [ ] All deployed resources are present and healthy.
- [ ] Container App baseline is HTTPS-only with replicas 3-5.
- [ ] The HTTPS root endpoint returns HTTP 200.
- [ ] All three SRE Agent managed-identity roles are resource-group scoped.
- [ ] All three runbooks are connected and indexed.
- [ ] Application Insights and Log Analytics are connected.
- [ ] Azure Monitor is connected.
- [ ] The autonomous response plan exactly matches `SRE demo availability failure`.
- [ ] Auto-remediation restores ingress and HTTPS health.
- [ ] Cost optimization changes only minimum replicas from 3 to 1.
- [ ] Security posture changes only `allowInsecure` from true to false.
- [ ] Reset succeeds between scenarios.
- [ ] The disposable resource group is deleted after the demonstration.

## References

- [Use-case overview](README.md)
- [Scenario control script](scripts/Set-SreDemoScenario.ps1)
- [Auto-remediation runbook](runbooks/auto-remediation.md)
- [Cost optimization runbook](runbooks/cost-optimization.md)
- [Security posture runbook](runbooks/security-posture.md)
- [Create and set up Azure SRE Agent](https://learn.microsoft.com/azure/sre-agent/create-and-set-up)
- [Automate incident response](https://learn.microsoft.com/azure/sre-agent/automate-incidents)
- [Execute mitigations](https://learn.microsoft.com/azure/sre-agent/execute-mitigations)
- [Schedule tasks](https://learn.microsoft.com/azure/sre-agent/scheduled-tasks)
- [Azure SRE Agent pricing and billing](https://learn.microsoft.com/azure/sre-agent/pricing-billing)
