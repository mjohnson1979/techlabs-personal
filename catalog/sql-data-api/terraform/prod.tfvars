# Production profile: zone-redundant app tier and zone-redundant SQL database.
environment        = "prod"
prefix             = "techlabs"
app_service_sku    = "P1v3"
app_capacity       = 3
app_zone_redundant = true
# Provisioned General Purpose with zone redundancy.
sql_sku_name       = "GP_Gen5_2"
sql_tier           = "GeneralPurpose"
sql_zone_redundant = true
# TODO: replace with your Entra user/group object id and display name.
sql_admin_object_id = "00000000-0000-0000-0000-000000000000"
sql_admin_login     = "sql-admins"
