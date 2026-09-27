"""CSV parsing and validation for STOCKY ingest (Requirement 1).

Turns an uploaded inventory CSV into validated :class:`~app.schemas.SkuRow`
records and a structured :class:`~app.schemas.UploadResult`. Pure and
deterministic: uses only the Python ``csv`` stdlib, performs no I/O beyond the
in-memory text it is given, and never touches the database or an LLM.

Validation rules (Req 1.1, 1.3, 1.4, 1.5):

- **Required columns** (Req 1.1, 1.3): the header must contain every column in
  :data:`REQUIRED_COLUMNS`. If any is missing, the WHOLE upload is rejected and
  the result names the (first) missing column via ``missing_column``; no rows
  are accepted.
- **Numeric fields** (Req 1.4): ``current_stock``, ``reorder_point`` and
  ``lead_time_days`` must parse as integers and ``unit_cost`` as a float. A row
  with a non-numeric value in any of these is rejected INDIVIDUALLY, carrying
  its source row number and a reason; other valid rows are still accepted.
- **Counts** (Req 1.5): the result reports the count of accepted records and the
  count of rejected rows.

Sales-history convention
-------------------------
``avg_daily_sales`` (a required numeric CSV column) is now the authoritative
Sales_Velocity source — it is used directly, never derived from
``sales_history``. ``sales_history`` is kept for backward-compat storage and
feeds ONLY the trend/anomaly window-halves detection rule downstream
(``detection/classify.py``); it is no longer read to compute velocity.

Recent daily sales history is supplied in a single ``sales_history`` column as a
delimited list of daily unit counts, most-recent-last. Both ``;`` and ``,`` are
accepted as separators (``,`` only works when the field is quoted in the CSV,
since ``,`` is also the CSV delimiter), e.g. ``"5;3;8;12"``. An empty
``sales_history`` value yields an empty list (treated downstream as no recent
sales). Any non-integer entry in the list makes the row invalid (Req 1.4).

Row numbering
-------------
Reported row numbers are 1-based and count the header as row 1, so the first
data row is row 2. This matches how a user sees the file in a spreadsheet.
"""

from __future__ import annotations

import csv
import datetime
import io
from typing import List, Optional, Tuple, Union

from app.schemas import RejectedRow, SkuRow, UploadResult

# Required header columns (Req 1.1). Order is irrelevant; presence is what matters.
REQUIRED_COLUMNS: Tuple[str, ...] = (
    "sku",
    "name",
    "category",
    "current_stock",
    "reorder_point",
    "lead_time_days",
    "unit_cost",
    "selling_price",
    "supplier_name",
    "avg_daily_sales",
    "sales_history",
    "last_sold_date",
)

# The numeric fields validated per Req 1.4, mapped to their parsers.
_INT_FIELDS: Tuple[str, ...] = ("current_stock", "reorder_point", "lead_time_days")
_FLOAT_FIELDS: Tuple[str, ...] = ("unit_cost", "selling_price", "avg_daily_sales")

# Separators accepted inside the sales_history list value.
_HISTORY_SEPARATORS = (";", ",")


class _RowError(Exception):
    """Raised internally when a data row fails validation; carries the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_csv(content: Union[str, bytes]) -> Tuple[List[SkuRow], UploadResult]:
    """Parse and validate inventory CSV content.

    Args:
        content: The raw CSV as text or bytes. Bytes are decoded as UTF-8
            (with a BOM tolerated). This is the shape a multipart upload
            delivers, so callers can pass the uploaded bytes directly.

    Returns:
        A ``(rows, result)`` tuple where ``rows`` is the list of accepted
        :class:`~app.schemas.SkuRow` records (empty when the whole upload is
        rejected) and ``result`` is the :class:`~app.schemas.UploadResult`
        describing accepted/rejected counts, per-row rejection reasons, and the
        missing column name when applicable (Req 1.3, 1.4, 1.5).

    Behaviour:
        - Missing required column → whole upload rejected: ``rows`` is empty,
          ``accepted == 0``, ``rejected_count == 0``, ``missing_column`` names
          the offending column (Req 1.3).
        - Otherwise each data row is validated independently; invalid rows are
          rejected with their row number and reason while valid rows are
          accepted (Req 1.4).
    """
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))

    # Header validation (Req 1.3). A header with no columns, or one missing any
    # required column, rejects the whole upload naming the missing column.
    header = reader.fieldnames or []
    missing = _first_missing_column(header)
    if missing is not None:
        return [], UploadResult(
            accepted=0,
            rejected_count=0,
            rejected=[],
            missing_column=missing,
        )

    accepted: List[SkuRow] = []
    rejected: List[RejectedRow] = []

    # Row 1 is the header, so the first data row is row 2.
    for offset, raw in enumerate(reader):
        row_number = offset + 2
        try:
            accepted.append(_parse_row(raw))
        except _RowError as err:
            rejected.append(RejectedRow(row=row_number, reason=err.reason))

    result = UploadResult(
        accepted=len(accepted),
        rejected_count=len(rejected),
        rejected=rejected,
        missing_column=None,
    )
    return accepted, result


def _decode(content: Union[str, bytes]) -> str:
    """Decode CSV content to text, tolerating a UTF-8 BOM."""
    if isinstance(content, bytes):
        return content.decode("utf-8-sig")
    # Strip a leading BOM if the text was decoded elsewhere without one.
    return content.lstrip("\ufeff")


def _first_missing_column(header: List[str]) -> Optional[str]:
    """Return the first required column absent from ``header``, else ``None``.

    Column names are compared case-insensitively and with surrounding
    whitespace ignored, so a header of ``"Current_Stock "`` still satisfies the
    ``current_stock`` requirement.
    """
    present = {(name or "").strip().lower() for name in header}
    for column in REQUIRED_COLUMNS:
        if column not in present:
            return column
    return None


def _parse_row(raw: dict) -> SkuRow:
    """Validate one raw CSV row into a :class:`~app.schemas.SkuRow`.

    Raises:
        _RowError: if a numeric field is non-numeric, a required text field is
            empty, or the sales-history list contains a non-integer (Req 1.4).
    """
    # Normalise keys so header casing/whitespace does not break field lookup.
    normalised = {(k or "").strip().lower(): (v if v is not None else "") for k, v in raw.items()}

    sku = normalised.get("sku", "").strip()
    name = normalised.get("name", "").strip()
    category = normalised.get("category", "").strip()
    supplier_name = normalised.get("supplier_name", "").strip()

    if not sku:
        raise _RowError("missing required value: sku")
    if not name:
        raise _RowError("missing required value: name")
    if not category:
        raise _RowError("missing required value: category")
    if not supplier_name:
        raise _RowError("missing required value: supplier_name")

    current_stock = _parse_int(normalised.get("current_stock", ""), "current_stock")
    reorder_point = _parse_int(normalised.get("reorder_point", ""), "reorder_point")
    lead_time_days = _parse_int(normalised.get("lead_time_days", ""), "lead_time_days")
    unit_cost = _parse_float(normalised.get("unit_cost", ""), "unit_cost")
    selling_price = _parse_float(normalised.get("selling_price", ""), "selling_price")
    avg_daily_sales = _parse_float(normalised.get("avg_daily_sales", ""), "avg_daily_sales")
    if avg_daily_sales < 0:
        raise _RowError(
            f"avg_daily_sales must not be negative: {normalised.get('avg_daily_sales', '')!r}"
        )
    sales_history = _parse_history(normalised.get("sales_history", ""))
    last_sold_date = _parse_date(normalised.get("last_sold_date", ""))

    return SkuRow(
        sku=sku,
        name=name,
        category=category,
        current_stock=current_stock,
        reorder_point=reorder_point,
        lead_time_days=lead_time_days,
        unit_cost=unit_cost,
        selling_price=selling_price,
        supplier_name=supplier_name,
        avg_daily_sales=avg_daily_sales,
        sales_history=sales_history,
        last_sold_date=last_sold_date,
    )


def _parse_int(value: str, field: str) -> int:
    """Parse an integer numeric field, raising ``_RowError`` if non-numeric (Req 1.4)."""
    text = (value or "").strip()
    try:
        # int(str) rejects floats like "3.5" and non-numeric text, which is the
        # intended strictness for these integer columns.
        return int(text)
    except (ValueError, TypeError):
        raise _RowError(f"non-numeric value in {field}: {value!r}")


def _parse_float(value: str, field: str) -> float:
    """Parse a float numeric field, raising ``_RowError`` if non-numeric (Req 1.4)."""
    text = (value or "").strip()
    try:
        return float(text)
    except (ValueError, TypeError):
        raise _RowError(f"non-numeric value in {field}: {value!r}")


def _parse_date(value: str) -> str:
    """Parse ``last_sold_date`` as an ISO date, raising ``_RowError`` if invalid.

    Uses ``datetime.date.fromisoformat`` so only real calendar dates in
    ``YYYY-MM-DD`` form are accepted (e.g. ``"2024-13-45"`` is rejected). The
    validated value is stored back via ``.isoformat()``, which round-trips a
    valid ``YYYY-MM-DD`` string unchanged.
    """
    text = (value or "").strip()
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise _RowError(f"invalid date in last_sold_date: {value!r}")


def _parse_history(value: str) -> List[int]:
    """Parse the sales-history list value into a list of integers.

    An empty value yields an empty list. Entries are split on ``;`` or ``,``;
    each entry must be an integer or the whole row is rejected (Req 1.4).
    """
    text = (value or "").strip()
    if not text:
        return []

    # Normalise all accepted separators to a single one before splitting.
    for sep in _HISTORY_SEPARATORS[1:]:
        text = text.replace(sep, _HISTORY_SEPARATORS[0])

    parts = [p.strip() for p in text.split(_HISTORY_SEPARATORS[0]) if p.strip() != ""]
    history: List[int] = []
    for part in parts:
        try:
            history.append(int(part))
        except (ValueError, TypeError):
            raise _RowError(f"non-numeric value in sales_history: {part!r}")
    return history
