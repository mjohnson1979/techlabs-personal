# Production profile: zone-redundant app tier and higher model throughput.
environment         = "prod"
prefix              = "techlabs"
app_service_sku     = "P1v3"
app_capacity        = 3
app_zone_redundant  = true
chat_model_name     = "gpt-5-mini"
chat_model_version  = "2025-08-07"
chat_model_sku      = "GlobalStandard"
chat_model_capacity = 60
