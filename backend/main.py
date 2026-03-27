import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from backend import storage  # noqa: E402 — must come after load_dotenv
from backend.mcp_server import mcp
from backend.routers import admin, public


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_tables_and_seed()
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
app.mount("/mcp", mcp.http_app())
