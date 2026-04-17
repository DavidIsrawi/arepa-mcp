import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from backend import storage  # noqa: E402 — must come after load_dotenv
from backend.mcp_server import mcp
from backend.routers import admin, public

logger = logging.getLogger("mcp.requests")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Build the MCP ASGI sub-app once so we can reuse its lifespan
mcp_app = mcp.http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_tables_and_seed()
    # FastMCP requires its own lifespan to start its session manager task group
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(
    title="Cupcake Store",
    description="Workshop demo — order cupcakes via your AI agent 🧁",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-please-change-in-production"),
    max_age=86400,  # 24 hours
    https_only=False,  # allow HTTP for local / workshop LAN
)


@app.middleware("http")
async def log_mcp_requests(request: Request, call_next):
    """Log incoming MCP requests (method, JSON-RPC method name, and tool args)."""
    if not request.url.path.startswith("/mcp"):
        return await call_next(request)

    body_summary = ""
    if request.method == "POST":
        body = await request.body()

        # Re-inject the body so downstream handlers can still read it
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]

        try:
            payload = json.loads(body or b"{}")
            rpc_method = payload.get("method", "?")
            params = payload.get("params", {}) or {}
            tool = params.get("name")
            args = params.get("arguments")
            if tool is not None:
                body_summary = f" {rpc_method} tool={tool} args={args}"
            else:
                body_summary = f" {rpc_method}"
        except Exception:
            body_summary = " <non-json body>"

    logger.info("MCP %s %s%s", request.method, request.url.path, body_summary)
    response = await call_next(request)
    logger.info("MCP %s %s → %s", request.method, request.url.path, response.status_code)
    return response


app.include_router(public.router)
app.include_router(admin.router)

# Mount the MCP server at /mcp (streamable HTTP transport)
app.mount("/mcp", mcp_app)
