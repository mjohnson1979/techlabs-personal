using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param storageSku = 'Standard_LRS'
param searchSku = 'basic'
param searchReplicas = 1
param searchPartitions = 1
