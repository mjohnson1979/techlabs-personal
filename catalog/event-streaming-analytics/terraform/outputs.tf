output "event_hub_namespace_fqdn" {
  value       = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net"
  description = "Fully qualified domain name of the Event Hubs namespace."
}

output "event_hub_name" {
  value       = azurerm_eventhub.telemetry.name
  description = "Name of the telemetry event hub."
}

output "streaming_job_name" {
  value       = azurerm_stream_analytics_job.this.name
  description = "Name of the Stream Analytics job."
}

output "cosmos_endpoint" {
  value       = azurerm_cosmosdb_account.this.endpoint
  description = "Cosmos DB account endpoint."
}

output "lake_account_name" {
  value       = azurerm_storage_account.lake.name
  description = "Name of the curated lake storage account."
}
