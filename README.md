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

---

## Tech stack

- **Python 3.11+** with [FastAPI](https://fastapi.tiangolo.com/)
- **Azure Table Storage** (or [Azurite](https://github.com/Azure/Azurite) for local dev)
- **Jinja2 + Tailwind CSS** for the dashboard and admin portal
- **[fastmcp](https://github.com/jlowin/fastmcp)** for the MCP server
- Sessions via `itsdangerous` signed cookies

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure storage

Copy the example env file:

```bash
cp .env.example .env
```

**Option A — Azurite (local, no Azure account needed):**

```bash
npm install -g azurite
azurite --location .azurite --silent &
```

The Azurite connection string is pre-filled in `.env.example` — no further changes needed.

**Option B — Real Azure Storage:**

Paste your connection string into `.env`:

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
```

### 3. Run

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

On first start, tables are created automatically and a default admin account is seeded.

---

## URLs

| URL | What |
|---|---|
| `http://localhost:8000/dashboard` | Beamer dashboard (project this!) |
| `http://localhost:8000/admin` | Admin portal |
| `http://localhost:8000/docs` | OpenAPI / Swagger |
| `http://localhost:8000/mcp` | MCP endpoint for agents |

---

## Default credentials

| Username | Password |
|---|---|
| `admin` | `admin` |

> **Change this** in the admin portal under **Users** before running a workshop.

---

## Admin portal

Log in at `/admin` to:

- **Orders** — see the live order queue, mark orders as *Ready* or *Collected*, and clear all orders between sessions
- **Flavours** — add flavours, set stock (how many cupcakes you actually have today), and use the inline +/− buttons for quick stock updates
- **Users** — add/remove admin accounts and change passwords

### Pre-workshop checklist

1. Bake/buy cupcakes, count them
2. Go to **Flavours** → set stock numbers to match what you have
3. Go to **Orders** → click **Clear All Orders** to start fresh
4. Open `/dashboard` fullscreen on the beamer

---

## MCP tools

Agents connect to `http://<host>:8000/mcp` using the **streamable HTTP** transport.

Three tools are available:

### `list_cupcakes()`
Returns all flavours that are currently in stock.

```json
[
  { "id": "choc-fudge", "name": "Chocolate Fudge", "description": "Rich dark ganache", "price": 2.50, "stock": 8 }
]
```

### `order_cupcake(flavor_id, customer_name)`
Places one order. The `customer_name` is called out loud at pickup (Starbucks-style).

```json
{ "success": true, "order_id": "...", "message": "Order placed! Listen for 'Alice' 🧁" }
```

### `check_order_status(customer_name)`
Returns the current status of an active order for that name.

---

## Guard rails

| Rule | How it's enforced |
|---|---|
| One cupcake per order | No `quantity` parameter exists |
| No double ordering | Rejected if the same name already has a pending or ready order |
| Out-of-stock protection | Stock is checked and decremented atomically before confirming |

---

## Example MCP client config (Claude Desktop)

```json
{
  "mcpServers": {
    "cupcake-store": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

---

## Project structure

```
cupcake-mcp/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── storage.py           # Azure Table Storage helpers
│   ├── auth.py              # Session auth dependency
│   ├── mcp_server.py        # MCP tools (fastmcp)
│   ├── routers/
│   │   ├── public.py        # /cupcakes, /orders, /dashboard
│   │   └── admin.py         # /admin/* routes
│   └── templates/           # Jinja2 templates (dashboard + admin)
├── .env.example
├── requirements.txt
└── README.md
```
