from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, List

from dateutil import parser as date_parser

# ---------------------------------------------------------------------------
# Core-sheet keywords (matched against the uploaded filename, not exact name).
# A file is "core" if its lowercased name CONTAINS one of these keywords.
# e.g. "margin_engine_data_healthy.csv" contains "margin" → core sheet.
# ---------------------------------------------------------------------------
CORE_SHEET_KEYWORDS = {
    "inventory",
    "sales",
    "purchases",
    "receivables",
    "payables",
    "invoices",
    "bank_statements",
    "batch_numbers",
    "grn",
    "margin",
    "deadstock",
    "credit",
}


def _is_core_sheet(file_name: str) -> bool:
    """Return True if the filename contains any core-sheet keyword."""
    lower = file_name.lower()
    return any(keyword in lower for keyword in CORE_SHEET_KEYWORDS)


# ---------------------------------------------------------------------------
# Strict ID-column detection.
# A column is an identifier only when its name (lowercased, stripped) is:
#   • exactly "id"
#   • ends with "_id"  (e.g. product_id, customer_id)
#   • ends with "id"   (e.g. orderid, invoiceid)
#   • ends with "no" / "num" / "number" / "ref" / "code"
#     (e.g. invoice_no, order_number, batch_ref)
# This deliberately excludes invoiceDate, totalInvoices, orderedQuantity, etc.
# ---------------------------------------------------------------------------
_ID_SUFFIXES = ("_id", "id", "no", "num", "number", "ref", "code")


def _is_id_column(header: str) -> bool:
    h = header.lower().strip()
    if h == "id":
        return True
    for suffix in _ID_SUFFIXES:
        if h.endswith(suffix) and len(h) > len(suffix):
            return True
    return False


@dataclass
class FileData:
    name: str
    data: List[dict[str, Any]]
    headers: List[str]


@dataclass
class SheetResult:
    file_name: str
    status: str
    row_count: int
    column_count: int
    issues: List[str]
    column_names: List[str]


@dataclass
class ValidationResult:
    sheet_results: dict[str, str]
    overall_decision: str
    reasons: List[str]


def parse_csv_bytes(file_name: str, raw: bytes) -> FileData:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    data = list(reader)
    return FileData(name=file_name, data=data, headers=headers)


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _try_parse_date(value: Any) -> bool:
    if not value:
        return True
    try:
        date_parser.parse(str(value))
        return True
    except Exception:
        return False


def _try_parse_number(value: Any) -> bool:
    if not value:
        return True
    try:
        cleaned = str(value).replace(",", "").replace("$", "")
        float(cleaned)
        return True
    except Exception:
        return False


def validate_sheet(file_data: FileData) -> SheetResult:
    issues: List[str] = []
    status = "PASS"

    if len(file_data.data) == 0:
        issues.append("File contains no data rows")
        status = "FAIL"
        return SheetResult(
            file_name=file_data.name,
            status=status,
            row_count=0,
            column_count=len(file_data.headers),
            issues=issues,
            column_names=file_data.headers,
        )

    if len(file_data.headers) == 0:
        issues.append("No column headers detected")
        status = "FAIL"

    header_set = set()
    duplicate_headers = []
    for header in file_data.headers:
        if header in header_set:
            duplicate_headers.append(header)
        header_set.add(header)
    if duplicate_headers:
        issues.append(f"Duplicate column names: {', '.join(duplicate_headers)}")
        if status == "PASS":
            status = "WARNING"

    empty_headers = [h for h in file_data.headers if not h.strip()]
    if empty_headers:
        issues.append(f"{len(empty_headers)} unnamed column(s) detected")
        if status == "PASS":
            status = "WARNING"

    total_cells = 0
    missing_cells = 0
    for row in file_data.data:
        for header in file_data.headers:
            total_cells += 1
            if _is_missing(row.get(header)):
                missing_cells += 1
    if total_cells > 0:
        missing_percentage = (missing_cells / total_cells) * 100
        if missing_percentage > 20:
            issues.append(f"High missing value rate: {missing_percentage:.1f}%")
            if status == "PASS":
                status = "WARNING"
        if missing_percentage > 50:
            issues.append("Critical: Over 50% of data is missing")
            status = "FAIL"

    date_columns = [
        h
        for h in file_data.headers
        if any(k in h.lower() for k in ["date", "time", "created", "updated"])
    ]
    for col in date_columns:
        invalid = [row for row in file_data.data if not _try_parse_date(row.get(col))]
        if invalid:
            issues.append(f'Invalid dates in column "{col}": {len(invalid)} rows')
            if status == "PASS":
                status = "WARNING"

    numeric_columns = [
        h
        for h in file_data.headers
        if any(
            k in h.lower()
            for k in ["amount", "quantity", "price", "total", "value", "balance"]
        )
    ]
    for col in numeric_columns:
        non_numeric = [row for row in file_data.data if not _try_parse_number(row.get(col))]
        if non_numeric:
            issues.append(f'Non-numeric values in "{col}": {len(non_numeric)} rows')
            if status == "PASS":
                status = "WARNING"

    # ------------------------------------------------------------------
    # Strict ID uniqueness check — uses _is_id_column() instead of broad
    # substring matching that previously caught invoiceDate, totalInvoices.
    # ------------------------------------------------------------------
    id_columns = [h for h in file_data.headers if _is_id_column(h)]
    for col in id_columns:
        values = [row.get(col) for row in file_data.data if row.get(col)]
        unique_values = set(values)
        if len(values) != len(unique_values):
            duplicate_count = len(values) - len(unique_values)
            issues.append(f'Duplicate identifiers in "{col}": {duplicate_count} duplicates')
            status = "FAIL"

    return SheetResult(
        file_name=file_data.name,
        status=status,
        row_count=len(file_data.data),
        column_count=len(file_data.headers),
        issues=issues,
        column_names=file_data.headers,
    )


def generate_validation_result(sheet_results: Iterable[SheetResult]) -> ValidationResult:
    sheet_map: dict[str, str] = {}
    reasons: List[str] = []
    results_list = list(sheet_results)

    for result in results_list:
        sheet_map[result.file_name] = result.status
        if result.issues:
            reasons.extend([f"[{result.file_name}] {issue}" for issue in result.issues])

    statuses = [r.status for r in results_list]

    # Use keyword-based matching so files like margin_engine_data_healthy.csv
    # are correctly treated as core sheets.
    core_sheet_results = [r for r in results_list if _is_core_sheet(r.file_name)]

    if any(r.status == "FAIL" for r in core_sheet_results):
        overall = "FAIL"
        reasons.insert(0, "One or more core sheets failed validation")
    elif "FAIL" in statuses or "WARNING" in statuses:
        overall = "CONDITIONAL PASS"
        if "WARNING" in statuses:
            reasons.insert(0, "Data passed with warnings requiring attention")
    else:
        overall = "PASS"

    return ValidationResult(
        sheet_results=sheet_map,
        overall_decision=overall,
        reasons=reasons,
    )


def normalize_rows(file_data: FileData) -> dict[str, Any]:
    return {
        "file_name": file_data.name,
        "row_count": len(file_data.data),
        "column_count": len(file_data.headers),
        "columns": file_data.headers,
        "records": file_data.data,
    }
