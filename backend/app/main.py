"""STOCKY FastAPI application entry point.

Creates the FastAPI app, registers API routers, and mounts the built frontend
as static files. Routers and the static build are wired in defensively: this
scaffold (task 1.1) precedes the router and frontend tasks, so each is attached
only when its module / build directory exists. That keeps the app importable
and startable from the very first task onward, with registration points ready
for later tasks to fill in.
"""

from importlib import import_module
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db

app = FastAPI(title="STOCKY Inventory Platform", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    """Create the database schema before serving requests.

    Ingest (``POST /api/inventory/upload``) and every other data path write to
    the inventory tables, so the schema must exist before the first request.
    ``init_db`` is idempotent (``create_all`` skips existing tables), making it
    safe to run on every startup.
    """
    init_db()


@app.get("/api/health")
def health() -> dict:
    """Liveness probe used to confirm the app is up."""
    return {"status": "ok"}


# --- Router registration ----------------------------------------------------
# Each router module exposes an APIRouter named ``router``. Modules are added
# by later tasks; we attach whichever already exist so the app always imports
# cleanly.
_ROUTER_MODULES = (
    "app.routers.ingest_router",
    "app.routers.inventory_router",
    "app.routers.investigation_router",
    "app.routers.review_router",
    "app.routers.dashboard_router",
    "app.routers.simulation_router",
    "app.routers.chaos_router",
    "app.routers.impact_router",
    "app.routers.uploads_router",
)


def _register_routers(application: FastAPI) -> None:
    for module_path in _ROUTER_MODULES:
        try:
            module = import_module(module_path)
        except ModuleNotFoundError:
            # Router not implemented yet — skip until its task lands.
            continue
        router = getattr(module, "router", None)
        if router is not None:
            application.include_router(router)


_register_routers(app)


# --- Static frontend mount --------------------------------------------------
# The Vite build lands in ``frontend/dist``. Mount it at root when present so a
# single container can serve API + SPA.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="static")
