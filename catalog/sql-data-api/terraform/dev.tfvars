environment        = "dev"
prefix             = "techlabs"
app_service_sku    = "B1"
app_capacity       = 1
app_zone_redundant = false
# Serverless General Purpose: auto-pauses, cheap for dev.
sql_sku_name       = "GP_S_Gen5_1"
sql_tier           = "GeneralPurpose"
sql_zone_redundant = false
# TODO: replace with your Entra user/group object id and display name.
sql_admin_object_id = "00000000-0000-0000-0000-000000000000"
sql_admin_login     = "sql-admins"
