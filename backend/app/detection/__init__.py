"""Deterministic Detection_Engine package.

Pure Python inventory analysis — metric computation and condition
classification — with no I/O and no LLM calls (Requirement 2.4). Modules here
take plain data in and return plain data out so they can be reused by ingest,
the read-only Simulation_Engine, and Chaos_Mode without side effects.
"""
