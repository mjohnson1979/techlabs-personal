# Production profile: zone-redundant environment, warm baseline (no cold starts).
environment     = "prod"
prefix          = "techlabs"
container_image = "mcr.microsoft.com/k8se/quickstart:latest"
cpu             = 1.0
memory          = "2.0Gi"
min_replicas    = 2
max_replicas    = 10
zone_redundant  = true
target_port     = 80
