# Production profile: zone-redundant Premium v3 plan across 3 availability zones.
environment    = "prod"
prefix         = "techlabs"
sku            = "P1v3"
capacity       = 3
zone_redundant = true
dotnet_version = "8.0"
