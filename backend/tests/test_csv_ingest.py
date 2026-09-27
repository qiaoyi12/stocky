"""Property 1 — CSV ingest round trip and count integrity.

Exercises :func:`app.ingest.csv_parser.parse_csv` and the deterministic
:func:`app.ingest.loader.load_rows` persistence path (design.md → Correctness
Properties → Property 1):

    For any CSV composed of a set of valid rows and a set of invalid rows
    (missing required column omitted per-file; non-numeric numeric fields
    per-row), the upload result satisfies
    ``accepted_count + rejected_count == total_rows``, every accepted record
    reads back from the store equal to its parsed input, and every rejected row
    is reported with its correct row number (or, for a missing column, the
    missing column name).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

Both a Hypothesis property (many generated CSVs mixing valid + invalid rows)
and a handful of explicit unit tests (empty file, quoted comma-separated sales
history, boundary row numbering) are included: the property proves the
invariants hold across the input space while the examples pin specific edge
cases and document intended behaviour.

The test builds CSV text from generated row *specs* rather than from
already-parsed rows, so the expected accepted/rejected split and the expected
1-based row numbers are known independently of the parser under test.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from typing import List, Optional, Tuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingest.csv_parser import REQUIRED_COLUMNS, parse_csv
from app.ingest.loader import load_rows
from app.repositories import sku_repo
from app.schemas import SkuRow


# --- Temp-DB session fixture (self-contained; does not touch conftest) ------


def _make_engine_and_session():
    """Build an isolated temp-file SQLite engine + session with the full schema.

    Returns ``(engine, session, path)`` so callers can dispose/remove after.
    Kept as a plain helper (not only a fixture) so the ``@given`` property can
    build a fresh DB per example — Hypothesis examples cannot reuse a
    function-scoped fixture.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True
    )
    # Import for the side effect of registering every model on Base.metadata.
    from app import models  # noqa: F401
    from app.db import Base

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, Session(), path


def _teardown(engine, session, path) -> None:
    session.close()
    engine.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture()
def session():
    """Yield a session bound to an isolated temp SQLite DB, torn down after."""
    engine, sess, path = _make_engine_and_session()
    try:
        yield sess
    finally:
        _teardown(engine, sess, path)


# --- CSV construction helpers -----------------------------------------------

# A "row spec" is either a valid SkuRow (we expect it accepted) or an invalid
# marker carrying the raw cell values (we expect it rejected). Building the CSV
# from these specs lets us compute the expected accepted/rejected split and the
# expected 1-based row numbers without trusting the parser.


def _csv_cell(value: str) -> str:
    """Serialise one cell, quoting so embedded commas/quotes survive the CSV."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="")
    writer.writerow([value])
    return out.getvalue()


def _build_csv(header: List[str], rows: List[List[str]]) -> str:
    """Render a CSV text from a header and a list of raw string rows."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def _valid_cells(row: SkuRow) -> List[str]:
    """The raw CSV cells (in REQUIRED_COLUMNS order) for a valid SkuRow."""
    # sales_history joined with ';' — the primary accepted separator.
    history = ";".join(str(u) for u in row.sales_history)
    mapping = {
        "sku": row.sku,
        "name": row.name,
        "category": row.category,
        "current_stock": str(row.current_stock),
        "reorder_point": str(row.reorder_point),
        "lead_time_days": str(row.lead_time_days),
        "unit_cost": _format_float(row.unit_cost),
        "selling_price": _format_float(row.selling_price),
        "supplier_name": row.supplier_name,
        "avg_daily_sales": _format_float(row.avg_daily_sales),
        "sales_history": history,
        "last_sold_date": row.last_sold_date,
    }
    return [mapping[col] for col in REQUIRED_COLUMNS]


def _format_float(value: float) -> str:
    """Format a float so it round-trips through ``float()`` back to itself."""
    return repr(value)


# --- Hypothesis strategies --------------------------------------------------

# Identifiers/text kept to plain printable ASCII without commas/quotes/newlines
# so the CSV shape stays simple; the point of this property is the numeric
# validation + counting, not CSV-dialect torture.
_text = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters=',"\\'),
    min_size=1,
    max_size=12,
).map(lambda s: s.strip()).filter(lambda s: s != "")

_ints = st.integers(min_value=-1000, max_value=100000)
_floats = st.floats(
    min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
).filter(lambda f: f == f)  # guard against NaN (belt and suspenders)
_history = st.lists(st.integers(min_value=0, max_value=500), min_size=0, max_size=8)
_iso_dates = st.dates(min_value=__import__("datetime").date(2000, 1, 1),
                       max_value=__import__("datetime").date(2100, 12, 31)).map(
    lambda d: d.isoformat()
)


@st.composite
def valid_rows(draw) -> SkuRow:
    """Generate a SkuRow whose serialised cells parse back to itself."""
    return SkuRow(
        sku=draw(_text),
        name=draw(_text),
        category=draw(_text),
        current_stock=draw(_ints),
        reorder_point=draw(_ints),
        lead_time_days=draw(_ints),
        unit_cost=draw(_floats),
        selling_price=draw(_floats),
        supplier_name=draw(_text),
        avg_daily_sales=draw(_floats),
        sales_history=draw(_history),
        last_sold_date=draw(_iso_dates),
    )


# A non-numeric token that will make whichever numeric field it lands in reject.
_bad_token = st.sampled_from(["abc", "1.2.3", "12x", "--", "n/a", "", " ", "3.5"])
# Note: "3.5" is non-integer, so it is a valid rejection trigger for int fields.

# Which numeric field to corrupt in an invalid row.
_numeric_fields = st.sampled_from(
    [
        "current_stock",
        "reorder_point",
        "lead_time_days",
        "unit_cost",
        "selling_price",
        "avg_daily_sales",
    ]
)


@st.composite
def invalid_rows(draw) -> Tuple[List[str], str]:
    """Generate raw cells for a row that must be rejected, plus the bad field.

    Starts from a valid row's cells then overwrites exactly one numeric field
    with a non-numeric/invalid token so the row is rejected individually
    (Req 1.4). Returns ``(cells, field)`` where ``field`` is the corrupted
    column name.
    """
    base = draw(valid_rows())
    cells = _valid_cells(base)
    field = draw(_numeric_fields)
    idx = REQUIRED_COLUMNS.index(field)
    bad = draw(_bad_token)
    # For float fields, "3.5" would actually be valid, so pick a non-float token.
    if field in ("unit_cost", "selling_price", "avg_daily_sales") and bad == "3.5":
        bad = "abc"
    cells[idx] = bad
    return cells, field


@st.composite
def csv_plan(draw):
    """Generate a mixed CSV plan: an ordered list of (kind, payload) rows.

    ``kind`` is ``"valid"`` (payload is a SkuRow) or ``"invalid"`` (payload is
    the raw cells list). The order is preserved so expected 1-based row numbers
    are deterministic (header is row 1, first data row is row 2).
    """
    n = draw(st.integers(min_value=0, max_value=6))
    plan = []
    for _ in range(n):
        if draw(st.booleans()):
            plan.append(("valid", draw(valid_rows())))
        else:
            cells, _field = draw(invalid_rows())
            plan.append(("invalid", cells))
    return plan


# --- Property 1: count integrity + row numbers + missing column -------------


@settings(deadline=None, max_examples=150)
@given(plan=csv_plan())
def test_property_count_integrity_and_row_numbers(plan):
    """accepted+rejected == total rows; rejected rows carry correct row numbers.

    Builds a CSV from a generated mix of valid and invalid rows, then asserts:
      * ``accepted + rejected_count == total_data_rows`` (Req 1.5)
      * the accepted count equals the number of generated valid rows and the
        rejected count equals the number of generated invalid rows (Req 1.4)
      * every rejected row's ``row`` field equals its true 1-based position
        (header is row 1, so a data row at index ``k`` is row ``k + 2``)
      * ``missing_column`` is ``None`` (header is always complete here)

    **Validates: Requirements 1.1, 1.4, 1.5**
    """
    header = list(REQUIRED_COLUMNS)
    rows_cells: List[List[str]] = []
    expected_valid = 0
    expected_invalid_row_numbers: List[int] = []

    for index, (kind, payload) in enumerate(plan):
        row_number = index + 2  # header is row 1
        if kind == "valid":
            rows_cells.append(_valid_cells(payload))
            expected_valid += 1
        else:
            rows_cells.append(payload)
            expected_invalid_row_numbers.append(row_number)

    csv_text = _build_csv(header, rows_cells)
    accepted, result = parse_csv(csv_text)

    total_rows = len(plan)
    # Count integrity (Req 1.5).
    assert result.accepted + result.rejected_count == total_rows
    assert result.accepted == len(accepted)
    assert result.accepted == expected_valid
    assert result.rejected_count == len(expected_invalid_row_numbers)
    assert result.missing_column is None

    # Every rejected row reports its correct 1-based row number (Req 1.4).
    reported = sorted(r.row for r in result.rejected)
    assert reported == sorted(expected_invalid_row_numbers)
    # Reasons are non-empty strings naming the failure.
    assert all(isinstance(r.reason, str) and r.reason for r in result.rejected)


# --- Property 1: round trip — accepted records equal parsed input -----------


def _skurow_equal(a: SkuRow, b: SkuRow) -> bool:
    """Field-by-field equality for two SkuRow records."""
    return (
        a.sku == b.sku
        and a.name == b.name
        and a.category == b.category
        and a.current_stock == b.current_stock
        and a.reorder_point == b.reorder_point
        and a.lead_time_days == b.lead_time_days
        and a.unit_cost == b.unit_cost
        and a.selling_price == b.selling_price
        and a.supplier_name == b.supplier_name
        and a.avg_daily_sales == b.avg_daily_sales
        and list(a.sales_history) == list(b.sales_history)
        and a.last_sold_date == b.last_sold_date
    )


@settings(deadline=None, max_examples=100)
@given(rows=st.lists(valid_rows(), min_size=0, max_size=6, unique_by=lambda r: r.sku))
def test_property_round_trip_parse_equals_input(rows):
    """Every accepted SkuRow equals the source row it was built from (Req 1.1).

    Serialises a set of valid rows to CSV, parses it, and asserts each accepted
    record matches the corresponding generated input field-by-field. Rows are
    unique by SKU so the ordered comparison is unambiguous.

    **Validates: Requirements 1.1**
    """
    header = list(REQUIRED_COLUMNS)
    csv_text = _build_csv(header, [_valid_cells(r) for r in rows])

    accepted, result = parse_csv(csv_text)

    assert result.rejected_count == 0
    assert result.missing_column is None
    assert len(accepted) == len(rows)
    for parsed, source in zip(accepted, rows):
        assert _skurow_equal(parsed, source)


@settings(deadline=None, max_examples=60)
@given(rows=st.lists(valid_rows(), min_size=1, max_size=6, unique_by=lambda r: r.sku))
def test_property_round_trip_through_store(rows):
    """Accepted rows persisted via load_rows read back equal to parsed input.

    Round trip through the Inventory_Store (Req 1.2): parse a valid CSV, load
    the accepted rows into a temp SQLite store via the deterministic loader,
    then read each SKU back through ``sku_repo`` and assert the persisted core
    fields and sales-history series match the parsed input.

    **Validates: Requirements 1.2**
    """
    header = list(REQUIRED_COLUMNS)
    csv_text = _build_csv(header, [_valid_cells(r) for r in rows])
    accepted, result = parse_csv(csv_text)
    assert result.rejected_count == 0

    engine, sess, path = _make_engine_and_session()
    try:
        loaded = load_rows(sess, accepted)
        sess.commit()
        assert loaded == len(accepted)

        for source in accepted:
            stored = sku_repo.get(sess, source.sku)
            assert stored is not None
            assert stored.current_stock == source.current_stock
            assert stored.reorder_point == source.reorder_point
            assert stored.lead_time_days == source.lead_time_days
            assert stored.unit_cost == source.unit_cost
            assert stored.name == source.name
            assert stored.category == source.category
            # Sales-history series preserved in order (oldest-first).
            assert sku_repo.get_sales_units(sess, source.sku) == list(source.sales_history)
    finally:
        _teardown(engine, sess, path)


# --- Property 1: missing required column rejects the whole upload -----------


@settings(deadline=None, max_examples=len(REQUIRED_COLUMNS) * 4)
@given(
    missing=st.sampled_from(REQUIRED_COLUMNS),
    rows=st.lists(valid_rows(), min_size=0, max_size=4, unique_by=lambda r: r.sku),
)
def test_property_missing_column_rejects_whole_upload(missing, rows):
    """Dropping any required column rejects the upload naming that column (Req 1.3).

    Builds a CSV whose header omits one required column (data rows still have
    otherwise-valid values for the remaining columns). Asserts zero accepted,
    zero rejected rows, and ``missing_column`` set to the dropped column.

    **Validates: Requirements 1.3**
    """
    header = [c for c in REQUIRED_COLUMNS if c != missing]
    # Build rows with cells only for the kept columns.
    keep_idx = [REQUIRED_COLUMNS.index(c) for c in header]
    rows_cells = []
    for r in rows:
        full = _valid_cells(r)
        rows_cells.append([full[i] for i in keep_idx])

    csv_text = _build_csv(header, rows_cells)
    accepted, result = parse_csv(csv_text)

    assert accepted == []
    assert result.accepted == 0
    assert result.rejected_count == 0
    assert result.rejected == []
    assert result.missing_column == missing


# --- Explicit unit tests: edge cases ----------------------------------------


def test_empty_content_reports_first_missing_column():
    """Empty/headerless content is treated as missing the first required column."""
    accepted, result = parse_csv("")
    assert accepted == []
    assert result.accepted == 0
    assert result.rejected_count == 0
    assert result.missing_column == REQUIRED_COLUMNS[0]


def test_all_valid_rows_accepted():
    """A clean CSV of two valid rows accepts both with no rejections (Req 1.5)."""
    header = list(REQUIRED_COLUMNS)
    rows = [
        ["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "3.5", "1;2;3", "2024-01-01"],
        ["B2", "Gadget", "toys", "0", "3", "2", "9.99", "14.99", "Supplier B", "1.0", "0;0;4", "2024-02-15"],
    ]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert result.accepted == 2
    assert result.rejected_count == 0
    assert accepted[0].sku == "A1"
    assert accepted[0].sales_history == [1, 2, 3]
    assert accepted[1].unit_cost == 9.99
    assert accepted[0].selling_price == 2.99
    assert accepted[0].supplier_name == "Supplier A"
    assert accepted[0].avg_daily_sales == 3.5
    assert accepted[0].last_sold_date == "2024-01-01"


def test_row_numbering_is_one_based_with_header_as_row_one():
    """A single bad row among valid rows reports its true 1-based row number."""
    header = list(REQUIRED_COLUMNS)
    rows = [
        ["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "3.5", "1;2;3", "2024-01-01"],  # row 2 (valid)
        ["B2", "Gadget", "toys", "NOPE", "3", "2", "9.99", "14.99", "Supplier B", "1.0", "0;0", "2024-01-01"],  # row 3 (invalid)
        ["C3", "Gizmo", "toys", "4", "2", "1", "3.00", "4.00", "Supplier C", "0.5", "5", "2024-01-01"],  # row 4 (valid)
    ]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert result.accepted == 2
    assert result.rejected_count == 1
    assert result.rejected[0].row == 3
    assert "current_stock" in result.rejected[0].reason


def test_quoted_comma_separated_sales_history_parses():
    """A quoted comma-separated sales_history is accepted (',' separator, Req 1.1)."""
    header = list(REQUIRED_COLUMNS)
    # csv.writer will quote the comma-containing history cell automatically.
    rows = [["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "4.0", "3,4,5", "2024-01-01"]]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert result.accepted == 1
    assert accepted[0].sales_history == [3, 4, 5]


def test_non_integer_sales_history_entry_rejects_row():
    """A non-integer entry in sales_history rejects that row (Req 1.4)."""
    header = list(REQUIRED_COLUMNS)
    rows = [["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "4.0", "3;x;5", "2024-01-01"]]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert accepted == []
    assert result.rejected_count == 1
    assert result.rejected[0].row == 2
    assert "sales_history" in result.rejected[0].reason


def test_missing_supplier_name_rejects_row():
    """An empty supplier_name rejects that row (Req: supplier_name required)."""
    header = list(REQUIRED_COLUMNS)
    rows = [["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "", "4.0", "1;2;3", "2024-01-01"]]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert accepted == []
    assert result.rejected_count == 1
    assert "supplier_name" in result.rejected[0].reason


def test_negative_avg_daily_sales_rejects_row():
    """A negative avg_daily_sales rejects that row."""
    header = list(REQUIRED_COLUMNS)
    rows = [["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "-1.0", "1;2;3", "2024-01-01"]]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert accepted == []
    assert result.rejected_count == 1
    assert "avg_daily_sales" in result.rejected[0].reason


def test_invalid_last_sold_date_rejects_row():
    """An invalid last_sold_date (not a real date) rejects that row."""
    header = list(REQUIRED_COLUMNS)
    rows = [["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "4.0", "1;2;3", "not-a-date"]]
    accepted, result = parse_csv(_build_csv(header, rows))
    assert accepted == []
    assert result.rejected_count == 1
    assert "last_sold_date" in result.rejected[0].reason

    rows2 = [["A1", "Widget", "tools", "10", "5", "7", "1.50", "2.99", "Supplier A", "4.0", "1;2;3", "2024-13-45"]]
    accepted2, result2 = parse_csv(_build_csv(header, rows2))
    assert accepted2 == []
    assert result2.rejected_count == 1
    assert "last_sold_date" in result2.rejected[0].reason
