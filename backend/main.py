import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from backend import storage  # noqa: E402 — must come after load_dotenv
from backend.mcp_server import mcp
from backend.routers import admin, public

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

app.include_router(public.router)
app.include_router(admin.router)

# Mount the MCP server at /mcp (streamable HTTP transport)
app.mount("/mcp", mcp_app)
