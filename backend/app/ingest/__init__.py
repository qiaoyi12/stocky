"""Ingest package: CSV parsing/validation and deterministic loading.

``csv_parser`` turns uploaded CSV text into validated :class:`~app.schemas.SkuRow`
records plus a structured :class:`~app.schemas.UploadResult` (accepted count,
rejected rows with reasons, and a missing-column name when the whole upload is
rejected). ``loader`` (task 2.2) persists parsed rows deterministically.
"""
