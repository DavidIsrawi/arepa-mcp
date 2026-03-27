using './main.bicep'

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'cupcake')
param location = readEnvironmentVariable('AZURE_LOCATION', 'westeurope')
