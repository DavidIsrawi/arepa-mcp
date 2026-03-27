targetScope = 'subscription'

@minLength(1)
@maxLength(20)
@description('Name of the environment (e.g. dev, prod, workshop)')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources')
param location string

var tags = { 'azd-env-name': environmentName }
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    environmentName: environmentName
  }
}

// ── Outputs consumed by azd ──────────────────────────────────────────────────
// azd uses AZURE_CONTAINER_REGISTRY_ENDPOINT to know where to push the image
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output AZURE_RESOURCE_GROUP string = rg.name
output SERVICE_APP_NAME string = resources.outputs.containerAppName
output SERVICE_APP_URI string = resources.outputs.containerAppUri
