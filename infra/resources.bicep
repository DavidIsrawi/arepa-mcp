param location string
param tags object
param resourceToken string
param environmentName string

// ── Storage Account ──────────────────────────────────────────────────────────
// Standard_LRS is sufficient for workshop; switch to ZRS/GRS for production
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'st${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

// ── Log Analytics (required by Container Apps Environment) ───────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ── Container Registry ───────────────────────────────────────────────────────
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: true
  }
}

// ── Container Apps Environment ───────────────────────────────────────────────
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'cae-${environmentName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Container App ────────────────────────────────────────────────────────────
// Initial image is a placeholder — azd will replace it with the built image on deploy
// The 'azd-service-name: app' tag links this resource to the 'app' service in azure.yaml
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'ca-${environmentName}'
  location: location
  tags: union(tags, { 'azd-service-name': 'app' })
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: registry.properties.loginServer
          username: registry.listCredentials().username
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: [
        {
          name: 'registry-password'
          value: registry.listCredentials().passwords[0].value
        }
        {
          name: 'storage-connection-string'
          value: storageConnectionString
        }
        {
          // Deterministic but opaque enough for a workshop session cookie secret
          name: 'session-secret'
          value: uniqueString(resourceToken, 'session-secret-v1')
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_STORAGE_CONNECTION_STRING'
              secretRef: 'storage-connection-string'
            }
            {
              name: 'SESSION_SECRET'
              secretRef: 'session-secret'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output registryLoginServer string = registry.properties.loginServer
output containerAppName string = containerApp.name
output containerAppUri string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
