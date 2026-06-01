@description('Azure region for all resources')
param location string = 'southafricanorth'

@description('Name prefix — all resources derive from this')
param appName string = 'newsaggregator'

var storageName = 'st${appName}${uniqueString(resourceGroup().id)}'
var funcAppName = 'func-${appName}'
var appInsightsName = 'appi-${appName}'
var queueArticleIngest = 'article-ingest'
var queueDeadLetter = 'article-dead-letter'
var containerName = 'news-data'
var tableName = 'ArticleIndex'
var planName = 'asp-${appName}'

// =============================================================================
// Storage Account — Blob, Queue, and Table
// =============================================================================
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource queueArticleIngestRes 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  name: '${storage.name}/default/${queueArticleIngest}'
}

resource queueDeadLetterRes 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  name: '${storage.name}/default/${queueDeadLetter}'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  name: '${storage.name}/default/${containerName}'
}

resource tableRes 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  name: '${storage.name}/default/${tableName}'
}

// =============================================================================
// Application Insights — Monitoring
// =============================================================================
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logWorkspace.id
  }
}

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${appName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// =============================================================================
// App Service Plan — Linux Consumption (serverless)
// =============================================================================
resource appPlan 'Microsoft.Web/serverFarms@2023-01-01' = {
  name: planName
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'   // Dynamic (Consumption)
    tier: 'Dynamic'
  }
  properties: {
    reserved: true   // Linux
  }
}

// =============================================================================
// Function App — Python 3.11 Linux Consumption
// =============================================================================
resource funcApp 'Microsoft.Web/sites@2023-01-01' = {
  name: funcAppName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'STORAGE_CONNECTION_STRING'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'QUEUE_NAME'
          value: queueArticleIngest
        }
        {
          name: 'DEAD_LETTER_QUEUE_NAME'
          value: queueDeadLetter
        }
        {
          name: 'CONTAINER_NAME'
          value: containerName
        }
        {
          name: 'TABLE_NAME'
          value: tableName
        }
      ]
    }
  }
}

// =============================================================================
// RBAC — Managed Identity → Storage access
// =============================================================================
var blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var queueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var tableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcApp.id, storage.id, 'blob')
  scope: storage
  properties: {
    roleDefinitionId: '${subscription().id}/providers/Microsoft.Authorization/roleDefinitions/${blobDataContributor}'
    principalId: funcApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource queueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcApp.id, storage.id, 'queue')
  scope: storage
  properties: {
    roleDefinitionId: '${subscription().id}/providers/Microsoft.Authorization/roleDefinitions/${queueDataContributor}'
    principalId: funcApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource tableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcApp.id, storage.id, 'table')
  scope: storage
  properties: {
    roleDefinitionId: '${subscription().id}/providers/Microsoft.Authorization/roleDefinitions/${tableDataContributor}'
    principalId: funcApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// =============================================================================
// Outputs
// =============================================================================
output functionAppName string = funcApp.name
output functionAppUrl string = 'https://${funcApp.properties.defaultHostName}'
output storageAccountName string = storage.name
output applicationInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output resourceGroup string = resourceGroup().name
output healthEndpoint string = 'https://${funcApp.properties.defaultHostName}/api/health'
