"""API router package for STOCKY.

Each module here exposes an ``APIRouter`` named ``router``; ``app.main``
auto-discovers and registers them. Routers are the HTTP edge only — they
delegate to repositories, services, and the agent orchestrator, and never
mutate inventory state themselves outside the deterministic write paths.
"""
