from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests

from .config import settings
from .models import EngineResult, PipelineResult, ValidationBundle
from .validation import (
    FileData,
    generate_validation_result,
    normalize_rows,
    parse_csv_bytes,
    validate_sheet,
)


def _now_run_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _ensure_export_dir() -> str:
    export_dir = settings.export_dir
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def _post_json(url: str | None, payload: dict[str, Any], engine_name: str) -> EngineResult:
    if not url:
        return EngineResult(
            engine=engine_name,
            status="SKIPPED",
            payload={"reason": "ENGINE_URL_NOT_SET"},
        )
    try:
        resp = requests.post(url, json=payload, timeout=settings.request_timeout_s)
        resp.raise_for_status()
        return EngineResult(engine=engine_name, status="OK", payload=resp.json())
    except Exception as exc:
        return EngineResult(
            engine=engine_name,
            status="ERROR",
            payload={"error": str(exc)},
        )


def _engine_to_export_dict(e: EngineResult) -> dict[str, Any]:
    """
    Always produce a fully-structured engine entry in exports.
    Guards against old/partial entries that only had {"reason": ...}.
    """
    return {
        "engine": e.engine,
        "status": e.status,
        "payload": e.payload if isinstance(e.payload, dict) else {"value": e.payload},
    }


def process_pipeline(
    files: Dict[str, Tuple[str, bytes]]
) -> PipelineResult:
    run_id = _now_run_id()

    file_data: Dict[str, FileData] = {}
    sheet_results = []

    for key, (name, raw) in files.items():
        parsed = parse_csv_bytes(name, raw)
        file_data[key] = parsed
        sheet_results.append(validate_sheet(parsed))

    validation_summary = generate_validation_result(sheet_results)
    validation_bundle = ValidationBundle(
        per_file=[
            {
                "file_name": r.file_name,
                "status": r.status,
                "row_count": r.row_count,
                "column_count": r.column_count,
                "issues": r.issues,
                "column_names": r.column_names,
            }
            for r in sheet_results
        ],
        summary={
            "sheet_results": validation_summary.sheet_results,
            "overall_decision": validation_summary.overall_decision,
            "reasons": validation_summary.reasons,
        },
    )

    normalized = {key: normalize_rows(fd) for key, fd in file_data.items()}

    errors: List[str] = []
    if validation_summary.overall_decision == "FAIL":
        errors.append("Validation failed. Engine processing skipped.")
        return PipelineResult(
            run_id=run_id,
            validation=validation_bundle,
            normalized=normalized,
            engines=[],
            planner={},
            export={},
            errors=errors,
        )

    margin_payload = {
        "run_id": run_id,
        "dataset": normalized.get("margin"),
        "validation": validation_summary.__dict__,
    }
    deadstock_payload = {
        "run_id": run_id,
        "dataset": normalized.get("deadstock"),
        "validation": validation_summary.__dict__,
    }
    credit_payload = {
        "run_id": run_id,
        "dataset": normalized.get("credit"),
        "validation": validation_summary.__dict__,
    }

    engines = [
        _post_json(settings.margin_engine_url, margin_payload, "margin-mastery"),
        _post_json(settings.deadstock_engine_url, deadstock_payload, "deadstock"),
        _post_json(settings.credit_engine_url, credit_payload, "credit-remove"),
    ]

    planner_payload = {
        "run_id": run_id,
        "engine_outputs": [e.payload for e in engines],
    }

    planner_result = {}
    if settings.planner_url:
        try:
            resp = requests.post(
                settings.planner_url, json=planner_payload, timeout=settings.request_timeout_s
            )
            resp.raise_for_status()
            planner_result = resp.json()
        except Exception as exc:
            planner_result = {"status": "ERROR", "error": str(exc)}
    else:
        planner_result = {
            "status": "SKIPPED",
            "reason": "PLANNER_URL_NOT_SET",
            "merged_actions": planner_payload["engine_outputs"],
        }

    export_dir = _ensure_export_dir()
    export_path = os.path.join(export_dir, f"profit-pathway-export-{run_id}.json")
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_id": run_id,
                "validation": validation_summary.__dict__,
                # Use helper to guarantee engine / status / payload keys are always present
                "engines": [_engine_to_export_dict(e) for e in engines],
                "planner": planner_result,
            },
            f,
            indent=2,
        )

    export = {
        "path": export_path,
        "format": "json",
    }

    return PipelineResult(
        run_id=run_id,
        validation=validation_bundle,
        normalized=normalized,
        engines=engines,
        planner=planner_result,
        export=export,
    )
