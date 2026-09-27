"""Data-access repositories for STOCKY.

Each module wraps SQLAlchemy session operations for one table group and keeps
persistence details out of the services, routers, and (crucially) the agents.

The single most important boundary in this package lives in ``sku_repo``: it is
split into a **read** section and a **write** section, and its inventory-write
functions are the ONLY place — alongside the ingest path, which uses this same
write path — that ``skus`` / ``sales_history`` rows are mutated. No agent or
orchestrator module imports those write functions; that isolation is what the
safety-boundary import-graph test asserts (design "Safety Boundary"; Req 9.3,
11.1).
"""
