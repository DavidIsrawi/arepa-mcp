# Arepa Lab — SHPE 2026 MCP Workshop

A hands-on workshop for the **2026 SHPE National Convention**, October 28–31 in
Indianapolis, Indiana. Attendees connect an AI agent to **Arepa Lab** through the
**Model Context Protocol (MCP)**, explore the menu, and place test or real pickup
orders that appear on a live dashboard.

Arepas are corn cakes enjoyed in Venezuela and Colombia. The store's original
minimal illustration shows a round grilled arepa with shredded beef and cheese,
paired with toasted-corn colors and walnut wood textures. The illustration is
branding, not an ingredient or allergen guarantee.

This is a workshop demonstration, not a production restaurant or payment system.
See the [official convention page](https://shpe.org/2026/) for event details.

---

## What's inside

| Component | Description |
|---|---|
| **FastAPI backend** | REST API for flavours, orders and admin operations |
| **Admin portal** | Web UI to manage flavours & stock, track orders, manage users |
| **Order dashboard** | Responsive, projector-friendly test/preparing/pickup queues |
| **MCP server** | Agents connect here to list arepas and place orders on behalf of a user |
| **Docker support** | `docker-compose.yml` with Azurite emulator for local dev |
| **Azure deployment** | GitHub Actions workflow to build & deploy to Azure Container Apps |

---

## Tech stack

- **Python 3.12** with [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- **Azure Table Storage** via `azure-data-tables` (or [Azurite](https://github.com/Azure/Azurite) for local dev)
- **Jinja2 + local CSS/SVG/JavaScript** for the dashboard; **Tailwind CSS** (CDN) plus the shared brand stylesheet for admin pages
- **[fastmcp](https://github.com/jlowin/fastmcp)** for the MCP server, mounted at `/mcp`
- **bcrypt** for password hashing
- **itsdangerous** signed cookies for admin sessions

---

## Project structure

```
arepa-mcp/
├── backend/
│   ├── main.py              # FastAPI app — wires up routers, session middleware, MCP mount
│   ├── storage.py           # Azure Table Storage client + all CRUD helpers
│   ├── auth.py              # require_admin FastAPI dependency
│   ├── mcp_server.py        # fastmcp tools: list_arepas, order_arepa, check_order_status
│   ├── ordering.py          # Shared MCP/REST validation, registration, menu and ordering
│   ├── branding.py          # SHPE event identity and workshop access configuration
│   ├── seed_menu.py         # Opt-in demo menu; never overwrites existing items
│   ├── static/             # Original arepa logo, wood pattern, shared CSS, dashboard JS
│   ├── routers/
│   │   ├── public.py        # GET /arepas, POST /orders, GET /dashboard
│   │   └── admin.py         # All /admin/* routes (login, orders, flavours, users, reset)
│   └── templates/
│       ├── dashboard.html   # Three live lanes; polls every 3 s without reloading
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

The workflow at `.github/workflows/deploy.yml` builds the `arepa-lab-mcp` Docker
image, pushes it to an Azure Container Registry, and updates an existing Azure
Container App. Changing the image name does not rename existing Azure resources.

**One-time setup (infrastructure):**

```bash
# Create RG, ACR, Storage, Container Apps Environment + App
az group create -n rg-arepa-mcp -l eastus
az acr create -g rg-arepa-mcp -n <acr-name> --sku Basic --admin-enabled true
az storage account create -g rg-arepa-mcp -n <storage-name> -l eastus --sku Standard_LRS --kind StorageV2
az containerapp env create -g rg-arepa-mcp -n cae-arepa-mcp -l eastus --logs-destination none
az containerapp create -g rg-arepa-mcp -n ca-arepa-mcp \
  --environment cae-arepa-mcp \
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
| `AZURE_RESOURCE_GROUP` | `rg-arepa-mcp` |
| `AZURE_CONTAINER_APP_NAME` | `ca-arepa-mcp` |
| `ACR_NAME` | Your ACR name (just the name, not FQDN) |

Push to `main` — or run the workflow manually — and the app will be deployed.

For every HTTPS deployment, set `SESSION_COOKIE_SECURE=true` on the Container
App so browsers send admin session cookies only over secure connections. Leave
it `false` only for local HTTP development.

Set `WORKSHOP_URL` and, if applicable, `WORKSHOP_TRAINING_KEY` as Container App
environment variables. Set `VOUCHER_SECRET` through an Azure secret reference if
using multiple replicas so they produce the same voucher. The deployment
workflow updates the image only; it does not configure these settings for you.

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
# In .env, uncomment the host-local connection string and remove the empty
# AZURE_STORAGE_CONNECTION_STRING= line. Set SESSION_SECRET to a random value.
```

Or set a real Azure Storage connection string in `.env`:
```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
```

**3. Run:**
```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

On first start, the four Azure tables (`Flavors`, `Orders`, `Users`, `Customers`)
are created and the default admin account is seeded. The menu is not created
automatically.

### Populate a local demo menu

With storage running and the same `.env` as the app:

```bash
python -m backend.seed_menu --stock 12
# Docker Compose alternative:
docker compose exec app python -m backend.seed_menu --stock 12
```

This adds three illustrative arepas at USD 0.00. Existing IDs, recipes, prices
and stock are never overwritten. Without `--stock`, new items have zero stock
and will not appear in `list_arepas` until staff add stock through **Admin → Menu**.
Confirm the actual menu, ingredients, prices and available portions before using
this with real food. The seed does not create orders or customers.

`python -m backend.seed_dummy_orders` creates fake customers and real-style orders
for dashboard rehearsal. It bypasses normal limits; use it only with disposable
demo storage, never the live conference database.

### Connect MCP Inspector

```bash
npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:8000/mcp/ --transport http
```

Open the Inspector UI URL printed in the terminal and connect its configured
HTTP server. The `/mcp/` endpoint is not a web page. Inspect **Tools** for the six
operations and **Prompts** for `agent_instructions` and `welcome_banner`.

### Run the example agent

Install `test-agents/requirements.txt` in a separate virtual environment. Configure
`FOUNDRY_MODEL_DEPLOYMENT`, `FOUNDRY_API_KEY` and `FOUNDRY_ENDPOINT` for your approved
Anthropic deployment, then run `python test-agents/agent.py`. Set `MCP_URL` for a
remote workshop server; it defaults to `http://127.0.0.1:8000/mcp/`.
Never commit API keys. The server itself does not need an AI model or API key.

---

## URLs

| URL | Description |
|---|---|
| `http://localhost:8000/dashboard` | Live order dashboard — project this on the screen |
| `http://localhost:8000/admin` | Admin portal (redirects to login) |
| `http://localhost:8000/docs` | OpenAPI / Swagger — interactive API docs |
| `http://localhost:8000/arepas` | `GET` — public list of flavours + stock |
| `http://localhost:8000/orders` | `GET` — full order records; requires admin login |
| `http://localhost:8000/mcp/` | MCP endpoint for agents |

---

## Default admin credentials

| Username | Password |
|---|---|
| `admin` | `admin` |

> **Change this** in the admin portal under **Users** before running a workshop.

---

## Admin portal

Log in at `/admin` for Preparing, Pickup, Orders, Menu, Customers and Users:

### Orders `/admin/orders`
- Live table of all orders with status badges (Pending / Ready / Collected)
- **Mark Ready** button — moves the order to the dashboard's pickup queue
- **Mark Collected** button — finalises the order
- **Clear All Orders** button — deletes orders only; it does **not** replenish stock or reset customer allowances.

### Menu `/admin/flavors`
- Add flavours (name, description, price, stock, image URL)
- Inline **+** / **−** stock buttons for fast adjustments during setup
- Edit details (name, price, description, image URL) via an inline form
- Delete a flavour

> Stock represents **real physical arepas**. Set it to the portions available.
> Real orders consume one unit; cancellation restores it. Test orders do not
> affect stock. Admin prices are shown in USD; the app does not collect payment.

### Users `/admin/users`
- Add admin users (username + password)
- Change any user's password
- Delete users (cannot delete your own account)

### Pre-workshop checklist

1. Confirm the actual arepa menu, dietary information, prices, quantities and pickup location.
2. Configure the workshop link and optional training key as described below.
3. Change the default admin password; configure deployment secrets and HTTPS.
4. Use an isolated conference database or explicitly plan data retention and allowance resets.
5. Open **Menu** and set stock to match the real food available.
6. Rehearse a test order, real order, ready/pickup transition and cancellation.
7. Project `/dashboard`, confirm readability, and distribute the deployed `/mcp/` URL.

---

## Live order dashboard

`/dashboard` is designed to be projected on a screen visible to everyone in the room:

- Walnut header/footer, warm cream queues and the Arepa Lab logo.
- Three lanes: **Test kitchen**, **Preparing**, and **Ready for pickup**.
- Updates every **3 seconds** without a page reload, with a visible connection status.
- Stacks vertically on phones; long queues can scroll. Each lane displays up to 39 orders.
- Test orders and recently collected orders remain visible for five minutes.
- The six-character **order voucher** rotates every three minutes. It is separate
  from any training key used to enter the workshop platform.

### Organizer configuration — required before SHPE

| Setting / task | What to provide |
|---|---|
| `WORKSHOP_URL` | The real `aka.ms` short link or other HTTP(S) workshop URL. Blank shows “Ask your facilitator”; no invented link is shipped. |
| `WORKSHOP_TRAINING_KEY` | `ArepasAtSHPE` for this workshop. Configure the external training platform to accept the same value. Blank hides this section. This is public display text, not a secret. |
| Menu and inventory | Replace illustrative recipes/prices with the actual food offered; confirm allergens with the caterer. |
| Deployment | Verify GitHub OIDC/repository variables, Azure resources, the `arepa-lab-mcp` image, and published MCP URL. |
| Secrets | Set `SESSION_SECRET`, optionally a shared `VOUCHER_SECRET`, and `SESSION_COOKIE_SECURE=true` for HTTPS. |
| External materials | Update presentation slides, QR codes, workshop landing page, client configurations and the platform's real access key to match. |

Use `.env` locally; Docker Compose passes the workshop variables through. On
Azure, configure them on the Container App. Restart the app after changing `.env`.
Brand/event constants live in `backend/branding.py`. Logo and texture are original
local SVGs; no external image host is required. No official SHPE logo is bundled.

### Client migration and preserved behavior

This release deliberately replaces `list_cupcakes`, `order_cupcake` and
`/cupcakes` with `list_arepas`, `order_arepa` and `/arepas`. There are no old-name
aliases. Update client instructions and refresh cached MCP tool discovery.

The storage tables, `flavor_id`/`flavor_name` fields and `/admin/flavors` routes
remain unchanged to preserve existing orders and data. No automatic data migration,
menu deletion or inventory reset occurs. Existing food names in a deployed database
must be updated by its operator.
Existing numeric prices are not currency-converted; review them before using the
USD labels with a previously populated database.

The allowance is still **one real order per customer ID for the database's lifetime**,
not verified conference identity or one per day. Clearing orders does not reset it.
MCP cancellation restores both stock and allowance; admin cancellation restores
stock only. These policies are unchanged by this branding update.

---

## MCP server

Agents connect to `http://<host>:8000/mcp/` using the **streamable HTTP** transport. No authentication is required — the MCP server is intentionally open for workshop demos.

> [!WARNING]
> Treat every deployment as a supervised, disposable workshop service. Do not
> expose this MCP endpoint as a general-purpose production service without an
> authentication layer, network controls, and abuse monitoring.

Six tools are available:

MCP and REST use the same validation and business logic in `backend/ordering.py`
for registration, menu projection and order placement. Both trim registration
fields, accept accented names and common name punctuation, and normalize customer
IDs to uppercase before validation. A valid voucher clears earlier failed attempts,
even if the order is subsequently rejected for another reason. REST retains its
HTTP status codes and response shapes; MCP retains its `success`/`message` responses.
The REST menu includes sold-out items and image URLs; the MCP menu omits them.

If a stock write raises, the service attempts to cancel the provisional order
and propagates the storage error. It does not refund stock: the write may have
committed before its response failed. Staff must reconcile inventory before a
retry. Cleanup failures are also logged and propagated.

Tool discovery includes explicit MCP behavior annotations:

| Tools | Read-only | Destructive | Idempotent |
|---|---|---|---|
| `list_arepas`, `check_order_status`, `voucher_status` | Yes | No | Yes |
| `register_customer` | No | No | No |
| `order_arepa`, `cancel_order` | No | Yes | No |

All tools set `openWorldHint: false` because they operate within this workshop
store. Ordering can consume stock and cancellation changes an existing order.
Neither mutation is advertised as safe to replay: test orders can be duplicated,
and cancellation targets the customer's currently active order rather than a
fixed order ID. These annotations describe behavior; they do not enforce
permissions or add retry/concurrency guarantees.

### `register_customer(first_name, last_name, city)`
Registers a customer and returns an 8-character `customer_id`. Agents retain
this ID for subsequent order and status calls.

### `list_arepas()`
Returns flavours with qualitative stock labels rather than exact inventory.

```json
[
  {
    "id": "arepa-lab-special",
    "name": "Arepa Lab Special",
    "description": "Demo recipe: shredded beef, cheese and black beans.",
    "price": 0.0,
    "stock_status": "plenty",
    "stock_label": "Plenty available"
  }
]
```

### `order_arepa(flavor_id, customer_id, kind, voucher_code)`
Places a `test` or `real` order for a registered customer. A valid rotating
voucher from the workshop dashboard is required.

```json
{
  "success": true,
  "order_id": "67e622c8-c888-4da7-a458-dc855e75cf53",
  "kind": "real",
  "flavor": "Arepa Lab Special",
  "message": "REAL order placed for Arepa Lab Special. Watch the big screen at the front of the room — when your order moves to READY, hand in your voucher code and come to the front to pick up your arepa."
}
```

Returns `success: false` with a descriptive message if:
- The customer ID or voucher is invalid
- The customer already has an active order of that kind
- The customer has already received their one real arepa
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

### Client-to-MCP regression gate

The paired `code-with-foundry-shpe-workshop` repository contains the v0 E2E
runner. Run it after changes to either the client or the MCP contract. For
sibling checkouts, from this server repository:

```bash
../code-with-foundry-shpe-workshop/.venv/bin/python \
  ../code-with-foundry-shpe-workshop/tests/e2e/run.py --suite all \
  --server-repo . --server-python .venv/bin/python
```

Adjust the interpreter paths to your actual client/server environments. The
runner starts isolated local services and covers all six tools through both a
deterministic MCP client and a real model-backed workshop conversation. The
agent suite uses the client's configured Foundry deployment and incurs model
usage; `--suite contract` needs no model key. See `tests/e2e/README.md` in the
client checkout for setup, coverage and failure artifacts. No deployed workshop
data is used.

---

## Guard rails (misuse prevention)

| Rule | How it's enforced |
|---|---|
| One arepa per order | No `quantity` parameter exists in the tool or API |
| One real arepa per customer | Customer record tracks lifetime real orders |
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
| `GET` | `/arepas` | List flavours with qualitative stock status |
| `POST` | `/orders` | Place a test or real order with a voucher |
| `GET` | `/dashboard` | Live order dashboard |
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
| RowKey | string | URL-safe slug e.g. `"arepa-lab-special"` |
| name | string | Display name |
| description | string | Short description |
| price | float | Display price in USD; no payment processing |
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
| status | string | `pending` / `ready` / `collected` / `cancelled` |
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
    "arepa-lab": {
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

**Remote / Azure deployment:**
```json
{
  "mcpServers": {
    "arepa-lab": {
      "url": "https://<your-container-app-host>/mcp/"
    }
  }
}
```

## Regression tests

After installing the application requirements, run:

```bash
python -m unittest discover -s tests -v
node --test tests/dashboard.test.cjs
```

The Python tests exercise the real storage helpers against an in-memory Azure
Table boundary double, the actual MCP server via its client, and FastAPI routes
via `TestClient`. They cover menu availability, stock thresholds, registration,
real/test orders, voucher checks, rate limiting, lifetime limits, cancellation,
admin authentication/CSRF, transitions, prompts, seed idempotency, templates and
workshop configuration. The Node tests cover live rendering, escaping, featured
orders, countdown, offline recovery and malformed responses.

Neither suite uses a cloud account, production data, or an AI model. The GitHub
Actions workflow `.github/workflows/tests.yml` runs both suites.
