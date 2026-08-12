# 🧁 Cupcake Store — MCP Workshop Demo

A workshop demo that shows how AI agents can interact with a real-world service via the **Model Context Protocol (MCP)**. Attendees connect their AI agent to the store's MCP server and order a cupcake by name — which shows up live on a beamer dashboard for staff to prepare and call out at pickup.

---

## What's inside

| Component | Description |
|---|---|
| **FastAPI backend** | REST API for flavours, orders and admin operations |
| **Admin portal** | Web UI to manage flavours & stock, track orders, manage users |
| **Beamer dashboard** | Full-screen live order queue — designed to be projected during the workshop |
| **MCP server** | Agents connect here to list cupcakes and place orders on behalf of a user |
| **Docker support** | `docker-compose.yml` with Azurite emulator for local dev |
| **Azure deployment** | GitHub Actions workflow to build & deploy to Azure Container Apps |

---

## Tech stack

- **Python 3.12** with [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- **Azure Table Storage** via `azure-data-tables` (or [Azurite](https://github.com/Azure/Azurite) for local dev)
- **Jinja2 + Tailwind CSS** (CDN) for the dashboard and admin portal
- **[fastmcp](https://github.com/jlowin/fastmcp)** for the MCP server, mounted at `/mcp`
- **bcrypt** for password hashing
- **itsdangerous** signed cookies for admin sessions

---

## Project structure

```
cupcake-mcp/
├── backend/
│   ├── main.py              # FastAPI app — wires up routers, session middleware, MCP mount
│   ├── storage.py           # Azure Table Storage client + all CRUD helpers
│   ├── auth.py              # require_admin FastAPI dependency
│   ├── mcp_server.py        # fastmcp tools: list_cupcakes, order_cupcake, check_order_status
│   ├── routers/
│   │   ├── public.py        # GET /cupcakes, POST /orders, GET /dashboard
│   │   └── admin.py         # All /admin/* routes (login, orders, flavours, users, reset)
│   └── templates/
│       ├── dashboard.html   # Beamer view — dark, large fonts, 5 s auto-refresh
│       ├── login.html       # Admin login page
│       ├── admin_base.html  # Shared admin layout with nav
│       ├── admin_orders.html
│       ├── admin_flavors.html
│       └── admin_users.html
├── .github/
│   └── workflows/
│       └── deploy.yml       # Build image → push to ACR → update Container App
├── Dockerfile               # python:3.12-slim, installs deps, runs uvicorn
├── docker-compose.yml       # app + azurite services for local Docker dev
├── .dockerignore
├── .env.example
├── .gitignore
└── requirements.txt
```

---

## Quick start

### Option A — Deploy to Azure via GitHub Actions

The workflow at `.github/workflows/deploy.yml` builds the Docker image, pushes it to an Azure Container Registry, and updates an existing Azure Container App.

**One-time setup (infrastructure):**

```bash
# Create RG, ACR, Storage, Container Apps Environment + App
az group create -n rg-cupcake-mcp -l eastus
az acr create -g rg-cupcake-mcp -n <acr-name> --sku Basic --admin-enabled true
az storage account create -g rg-cupcake-mcp -n <storage-name> -l eastus --sku Standard_LRS --kind StorageV2
az containerapp env create -g rg-cupcake-mcp -n cae-cupcake-mcp -l eastus --logs-destination none
az containerapp create -g rg-cupcake-mcp -n ca-cupcake-mcp \
  --environment cae-cupcake-mcp \
  --image mcr.microsoft.com/k8se/quickstart:latest \
  --target-port 8000 --ingress external \
  --secrets storage-conn="<conn-str>" session-secret="<random>" \
  --env-vars AZURE_STORAGE_CONNECTION_STRING=secretref:storage-conn SESSION_SECRET=secretref:session-secret SESSION_COOKIE_SECURE=true
```

**One-time setup (GitHub OIDC):** Create an Entra ID app registration with a federated credential for this repo/branch, grant it `AcrPush` on the registry and `Contributor` on the resource group.

**Set these GitHub repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_TENANT_ID` | Entra tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID |
| `AZURE_RESOURCE_GROUP` | `rg-cupcake-mcp` |
| `AZURE_CONTAINER_APP_NAME` | `ca-cupcake-mcp` |
| `ACR_NAME` | Your ACR name (just the name, not FQDN) |

Push to `main` — or run the workflow manually — and the app will be deployed.

For every HTTPS deployment, set `SESSION_COOKIE_SECURE=true` on the Container
App so browsers send admin session cookies only over secure connections. Leave
it `false` only for local HTTP development.

---

### Option B — Docker Compose (local)

```bash
cp .env.example .env
```

Edit `.env` and set the Azurite connection string:
```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;TableEndpoint=http://azurite:10002/devstoreaccount1;
```

Then:
```bash
docker compose up --build
```

The `docker-compose.yml` starts two services:
- **`app`** — the FastAPI container (port 8000)
- **`azurite`** — Azure Table Storage emulator (port 10002), with a healthcheck so the app waits for it to be ready

The optional client in `test-agents/` has its own requirements file and should
be installed in a separate virtual environment from the application.

To use real Azure Storage instead, set your connection string in `.env` and remove the `azurite` service block from `docker-compose.yml`.

---

### Option C — Local Python

**1. Install dependencies:**
```bash
pip install -r requirements.txt
```

**2. Configure storage:**
```bash
cp .env.example .env
```

Local dev with Azurite:
```bash
npm install -g azurite
azurite --location .azurite --silent &
# Connection string is pre-filled in .env.example for host-local Azurite
```

Or set a real Azure Storage connection string in `.env`:
```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
```

**3. Run:**
```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

On first start, the three Azure tables (`Flavors`, `Orders`, `Users`) are created automatically and the default admin account is seeded.

---

## URLs

| URL | Description |
|---|---|
| `http://localhost:8000/dashboard` | Beamer dashboard — project this on the screen |
| `http://localhost:8000/admin` | Admin portal (redirects to login) |
| `http://localhost:8000/docs` | OpenAPI / Swagger — interactive API docs |
| `http://localhost:8000/cupcakes` | `GET` — public list of flavours + stock |
| `http://localhost:8000/orders` | `GET` — public list of all orders |
| `http://localhost:8000/mcp` | MCP endpoint for agents |

---

## Default admin credentials

| Username | Password |
|---|---|
| `admin` | `admin` |

> **Change this** in the admin portal under **Users** before running a workshop.

---

## Admin portal

Log in at `/admin` to access three management sections:

### Orders `/admin/orders`
- Live table of all orders with status badges (Pending / Ready / Collected)
- **Mark Ready** button — moves the order to the "Ready for pickup" section on the beamer
- **Mark Collected** button — finalises the order
- **Clear All Orders** button — deletes every order (does NOT touch stock). Use this between workshop sessions.

### Flavours `/admin/flavors`
- Add flavours (name, description, price, stock, image URL)
- Inline **+** / **−** stock buttons for fast adjustments during setup
- Edit details (name, price, description, image URL) via an inline form
- Delete a flavour

> Stock represents **real physical cupcakes**. Set the stock before the workshop starts to match how many you actually have. It is never auto-restored — only decrements as orders come in.

### Users `/admin/users`
- Add admin users (username + password)
- Change any user's password
- Delete users (cannot delete your own account)

### Pre-workshop checklist

1. Bake/buy cupcakes and count them
2. Open **Flavours** → set stock numbers to match
3. Open **Orders** → click **Clear All Orders** to start fresh
4. Open `/dashboard` fullscreen on the beamer
5. Distribute the MCP server URL to attendees

---

## Beamer dashboard

`/dashboard` is designed to be projected on a screen visible to everyone in the room:

- **Dark background**, large high-contrast fonts — readable from the back of a room
- Left column: **Preparing…** — pending orders queue (name + flavour)
- Right column: **Ready for Pickup!** — pulsing green cards with the customer name
- Auto-refreshes every **5 seconds** via `<meta http-equiv="refresh">` — no JavaScript framework needed

---

## MCP server

Agents connect to `http://<host>:8000/mcp` using the **streamable HTTP** transport. No authentication is required — the MCP server is intentionally open for workshop demos.

> [!WARNING]
> Treat every deployment as a supervised, disposable workshop service. Do not
> expose this MCP endpoint as a general-purpose production service without an
> authentication layer, network controls, and abuse monitoring.

Six tools are available:

### `register_customer(first_name, last_name, city)`
Registers a customer and returns an 8-character `customer_id`. Agents retain
this ID for subsequent order and status calls.

### `list_cupcakes()`
Returns flavours with qualitative stock labels rather than exact inventory.

```json
[
  {
    "id": "choc-fudge",
    "name": "Chocolate Fudge",
    "description": "Rich dark chocolate ganache",
    "price": 2.50,
    "stock_status": "in_stock",
    "stock_label": "plenty"
  }
]
```

### `order_cupcake(flavor_id, customer_id, kind, voucher_code)`
Places a `test` or `real` order for a registered customer. A valid rotating
voucher from the workshop dashboard is required.

```json
{ "success": true, "customer_id": "ABCD2345", "kind": "real", "status": "pending" }
```

Returns `success: false` with a descriptive message if:
- The customer ID or voucher is invalid
- The customer already has an active order of that kind
- The customer has already received their one real cupcake
- The flavour doesn't exist
- The flavour is out of stock

### `check_order_status(customer_id)`
Returns the status of the customer's active real order.

### `cancel_order(customer_id, kind)`
Cancels a pending order. Cancelling a real order restores its stock and the
customer's real-order allowance.

### `voucher_status()`
Returns timing information for the rotating workshop voucher without revealing
the voucher code.

---

## Guard rails (misuse prevention)

| Rule | How it's enforced |
|---|---|
| One cupcake per order | No `quantity` parameter exists in the tool or API |
| One real cupcake per customer | Customer record tracks lifetime real orders |
| No double ordering | Rejected if the customer has an active order of that kind |
| Out-of-stock protection | Stock checked before accepting; decremented immediately on order |
| Voucher validation | Orders require the rotating code displayed at the workshop |
| Abuse throttling | Repeated incorrect voucher attempts trigger a temporary lockout |

---

## REST API

The full API is documented at `/docs`. Key public endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/customers` | Register a customer and receive a customer ID |
| `GET` | `/cupcakes` | List flavours with qualitative stock status |
| `POST` | `/orders` | Place a test or real order with a voucher |
| `GET` | `/dashboard` | Beamer HTML view |
| `GET` | `/dashboard/data` | Display-safe dashboard data |

Key admin endpoints (require login session):

| Method | Path | Description |
|---|---|---|
| `GET/POST` | `/admin/login` | Login form |
| `POST` | `/admin/logout` | Logout |
| `GET` | `/admin/orders` | Order management page |
| `GET` | `/orders` | List complete order records as JSON |
| `POST` | `/admin/orders/{id}/status` | Update order status |
| `POST` | `/admin/reset` | Clear all orders |
| `GET` | `/admin/flavors` | Flavour management page |
| `POST` | `/admin/flavors` | Create flavour |
| `POST` | `/admin/flavors/{id}/edit` | Update flavour |
| `POST` | `/admin/flavors/{id}/delete` | Delete flavour |
| `GET` | `/admin/users` | User management page |
| `POST` | `/admin/users` | Create user |
| `POST` | `/admin/users/{username}/password` | Change password |
| `POST` | `/admin/users/{username}/delete` | Delete user |

---

## Data model (Azure Table Storage)

Four tables are created automatically on first startup:

**`Flavors`**
| Field | Type | Description |
|---|---|---|
| PartitionKey | `"flavor"` | Fixed |
| RowKey | string | URL-safe slug e.g. `"choc-fudge"` |
| name | string | Display name |
| description | string | Short description |
| price | float | Price in local currency |
| stock | int | Current physical stock |
| image_url | string | Optional image URL |

**`Orders`**
| Field | Type | Description |
|---|---|---|
| PartitionKey | `"order"` | Fixed |
| RowKey | UUID | Order ID |
| flavor_id | string | FK → Flavors RowKey |
| customer_id | string | FK → Customers RowKey |
| customer_name | string | Display name |
| kind | string | `test` / `real` |
| status | string | `pending` / `ready` / `collected` |
| created_at | ISO 8601 | Order timestamp |

**`Customers`**
| Field | Type | Description |
|---|---|---|
| PartitionKey | `"customer"` | Fixed |
| RowKey | string | 8-character customer ID |
| display_name | string | Customer display name |
| city | string | Customer city |
| real_orders_count | integer | Lifetime real-order count |

**`Users`**
| Field | Type | Description |
|---|---|---|
| PartitionKey | `"user"` | Fixed |
| RowKey | string | Username |
| password_hash | string | bcrypt hash |

---

## MCP client config examples

**MCP-compatible client:**
```json
{
  "mcpServers": {
    "cupcake-store": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

**Remote / Azure deployment:**
```json
{
  "mcpServers": {
    "cupcake-store": {
      "url": "https://ca-cupcake.<region>.azurecontainerapps.io/mcp"
    }
  }
}
```

