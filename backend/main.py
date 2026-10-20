import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from backend import storage  # noqa: E402 — must come after load_dotenv
from backend.branding import EVENT_NAME, STORE_NAME, workshop_context
from backend.mcp_server import mcp
from backend.routers import admin, public

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Build the MCP ASGI sub-app once so we can reuse its lifespan
mcp_app = mcp.http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    workshop_context()
    storage.ensure_tables_and_seed()
    # FastMCP requires its own lifespan to start its session manager task group
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(
    title=STORE_NAME,
    description=f"Order arepas with your AI agent — a workshop demo for the {EVENT_NAME}.",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-secret-please-change-in-production"),
    max_age=86400,  # 24 hours
    https_only=os.environ.get("SESSION_COOKIE_SECURE", "false").lower()
    in {"1", "true", "yes"},
)

app.include_router(public.router)
app.include_router(admin.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# Mount the MCP server at /mcp (streamable HTTP transport)
app.mount("/mcp", mcp_app)
