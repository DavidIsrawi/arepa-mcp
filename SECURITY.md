# Security Policy

## Supported versions

Security fixes are applied to the latest revision on the `main` branch.

## Reporting a vulnerability

Please report vulnerabilities through GitHub's private vulnerability reporting
feature instead of opening a public issue. Include reproduction steps, affected
endpoints, and the potential impact when possible.

This application is designed for supervised workshop demonstrations. The MCP
endpoint intentionally accepts unauthenticated traffic and must not be exposed
as a general-purpose production service without an authentication layer and
appropriate network controls.

## Repository owner checklist

After making the repository public, enable these GitHub settings:

- Private vulnerability reporting
- Secret scanning and push protection
- Dependabot alerts and security updates
- Branch protection for `main`, including pull requests and required checks
- Deployment environment approvals for production

Dependabot version-update configuration is committed in
`.github/dependabot.yml`. The settings above must be enabled by a repository
owner in GitHub because they are not controlled by repository files.