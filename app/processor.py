from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse, urlunparse

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

_DISCOVERY_CACHE: dict[str, str] = {}
_LAST_DISCOVERY_SCAN_AT = 0.0
_DISCOVERY_SCAN_TTL_S = 15.0
_DISCOVERY_CONNECT_TIMEOUT_S = 0.2
_DISCOVERY_READ_TIMEOUT_S = 0.4
_MAX_NETSTAT_DISCOVERY_PORTS = 40
_COMMON_DISCOVERY_PORTS = [
    9001,
    9002,
    9003,
    9004,
    8080,
    8081,
    8082,
    8083,
    5173,
    5174,
    5175,
    5176,
]
_ENGINE_ALIASES = {
    "margin-mastery": {"margin-mastery", "margin"},
    "deadstock": {"deadstock"},
    "credit-remove": {"credit-remove", "credit"},
    "planner": {"planner"},
}
_ENGINE_ENV_NAMES = {
    "margin-mastery": "MARGIN_ENGINE_URL",
    "deadstock": "DEADSTOCK_ENGINE_URL",
    "credit-remove": "CREDIT_ENGINE_URL",
    "planner": "PLANNER_URL",
}


def _now_run_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _ensure_export_dir() -> str:
    export_dir = settings.export_dir
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def _serialize_engine_result(engine: EngineResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(engine, EngineResult):
        return engine.dict()

    payload = engine.get("payload")
    if not isinstance(payload, dict):
        payload = {
            k: v for k, v in engine.items() if k not in {"engine", "status", "payload"}
        }
        if not payload:
            payload = {"raw": engine}

    inferred_status = "SKIPPED" if "reason" in payload else "UNKNOWN"
    return {
        "engine": str(engine.get("engine") or "unknown-engine"),
        "status": str(engine.get("status") or inferred_status),
        "payload": payload,
    }


def _normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().strip('"').strip("'").replace(" ", "")
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"http://{cleaned}"
    parsed = urlparse(cleaned)
    if not parsed.netloc:
        return None
    return cleaned


def _to_process_url(value: str | None) -> str | None:
    url = _normalize_url(value)
    if not url:
        return None
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        path = "/process"
    elif not path.endswith("/process"):
        path = f"{path}/process"
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def _to_health_url(process_url: str) -> str:
    parsed = urlparse(process_url)
    return urlunparse(parsed._replace(path="/health", params="", query="", fragment=""))


def _matches_engine(target_engine: str, reported_engine: str | None) -> bool:
    aliases = _ENGINE_ALIASES.get(target_engine, {target_engine})
    value = str(reported_engine or "").lower()
    return any(alias in value for alias in aliases)


def _canonical_engine_from_reported(reported_engine: str | None) -> str | None:
    value = str(reported_engine or "").lower()
    if not value:
        return None
    for canonical, aliases in _ENGINE_ALIASES.items():
        if any(alias in value for alias in aliases):
            return canonical
    return None


def _probe_reported_engine(process_url: str) -> str | None:
    try:
        health_resp = requests.get(
            _to_health_url(process_url),
            timeout=(_DISCOVERY_CONNECT_TIMEOUT_S, _DISCOVERY_READ_TIMEOUT_S),
        )
        health_resp.raise_for_status()
        body = health_resp.json()
        return str(body.get("engine") or "")
    except Exception:
        return None


def _probe_process_url(process_url: str, target_engine: str) -> bool:
    return _matches_engine(target_engine, _probe_reported_engine(process_url))


def _parse_listening_port(address: str) -> int | None:
    if not address:
        return None
    if address.startswith("["):
        match = re.search(r"\]:(\d+)$", address)
        if match:
            return int(match.group(1))
        return None
    if ":" not in address:
        return None
    tail = address.rsplit(":", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return None


def _list_local_ports_from_netstat() -> list[int]:
    ports: set[int] = set()
    commands: list[list[str]] = []
    if os.name == "nt":
        commands.append(["netstat", "-ano", "-p", "tcp"])
    else:
        commands.extend([["ss", "-ltn"], ["netstat", "-ltn"]])

    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            continue

        stdout = proc.stdout or ""
        for line in stdout.splitlines():
            upper = line.upper()
            if "LISTEN" not in upper:
                continue
            columns = line.split()
            if os.name == "nt":
                if len(columns) < 2:
                    continue
                address = columns[1]
            else:
                if len(columns) < 4:
                    continue
                address = columns[3]
            port = _parse_listening_port(address)
            if port and 1024 <= port <= 65535:
                ports.add(port)
        if ports:
            break
    return sorted(ports)[:_MAX_NETSTAT_DISCOVERY_PORTS]


def _discovery_ports() -> list[int]:
    ports: list[int] = []
    raw = os.getenv("ENGINE_DISCOVERY_PORTS", "")
    for piece in raw.split(","):
        piece = piece.strip()
        if piece.isdigit():
            port = int(piece)
            if 1 <= port <= 65535:
                ports.append(port)
    for port in _COMMON_DISCOVERY_PORTS:
        if port not in ports:
            ports.append(port)
    return ports


def _discover_engine_url(target_engine: str, exclude: set[str] | None = None) -> str | None:
    global _LAST_DISCOVERY_SCAN_AT
    exclude = exclude or set()
    cached = _DISCOVERY_CACHE.get(target_engine)
    if cached and cached not in exclude:
        return cached

    # Probe candidate ports once and populate cache for all engines.
    now = time.time()
    if now - _LAST_DISCOVERY_SCAN_AT > _DISCOVERY_SCAN_TTL_S:
        _LAST_DISCOVERY_SCAN_AT = now
        tried_ports: set[int] = set()
        candidate_ports: list[int] = []
        for port in _discovery_ports() + _list_local_ports_from_netstat():
            if port in tried_ports:
                continue
            tried_ports.add(port)
            candidate_ports.append(port)

        for port in candidate_ports:
            process_url = f"http://127.0.0.1:{port}/process"
            reported = _probe_reported_engine(process_url)
            canonical = _canonical_engine_from_reported(reported)
            if canonical and process_url not in exclude:
                _DISCOVERY_CACHE[canonical] = process_url

    cached = _DISCOVERY_CACHE.get(target_engine)
    if cached and cached not in exclude:
        return cached
    return None


def _resolve_process_url(configured_url: str | None, target_engine: str) -> str | None:
    explicit = _to_process_url(configured_url)
    if explicit:
        return explicit
    return _discover_engine_url(target_engine)


def _write_export(
    run_id: str,
    validation_summary: Any,
    engines: List[EngineResult | dict[str, Any]],
    planner_result: dict[str, Any],
    errors: List[str] | None = None,
) -> dict[str, Any]:
    export_dir = _ensure_export_dir()
    export_path = os.path.join(export_dir, f"profit-pathway-export-{run_id}.json")
    serialized_engines = [_serialize_engine_result(e) for e in engines]

    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_id": run_id,
                "validation": validation_summary.__dict__,
                "engines": serialized_engines,
                "planner": planner_result,
                "errors": errors or [],
            },
            f,
            indent=2,
        )

    return {
        "path": export_path,
        "format": "json",
    }


def _post_json(url: str | None, payload: dict[str, Any], engine_name: str) -> EngineResult:
    resolved_url = _resolve_process_url(url, engine_name)
    if not resolved_url:
        env_name = _ENGINE_ENV_NAMES.get(engine_name, "ENGINE_URL")
        return EngineResult(
            engine=engine_name,
            status="SKIPPED",
            payload={
                "reason": "ENGINE_URL_NOT_SET",
                "environment_variable": env_name,
                "hint": (
                    f"Set {env_name} to the deployed engine API base URL. "
                    "The service must return JSON from GET /health and POST /process."
                ),
            },
        )
    try:
        resp = requests.post(resolved_url, json=payload, timeout=settings.request_timeout_s)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            body.setdefault("endpoint_used", resolved_url)
        return EngineResult(engine=engine_name, status="OK", payload=body)
    except Exception as exc:
        fallback_url = _discover_engine_url(engine_name, exclude={resolved_url})
        if fallback_url and fallback_url != resolved_url:
            try:
                resp = requests.post(
                    fallback_url, json=payload, timeout=settings.request_timeout_s
                )
                resp.raise_for_status()
                body = resp.json()
                if isinstance(body, dict):
                    body.setdefault("endpoint_used", fallback_url)
                return EngineResult(engine=engine_name, status="OK", payload=body)
            except Exception:
                pass
        return EngineResult(
            engine=engine_name,
            status="ERROR",
            payload={"error": str(exc), "endpoint_used": resolved_url},
        )


def process_pipeline(
    files: Dict[str, Tuple[str, bytes]]
) -> PipelineResult:
    run_id = _now_run_id()

    file_data: Dict[str, FileData] = {}
    sheet_results = []

    for key, (name, raw) in files.items():
        parsed = parse_csv_bytes(name, raw, source_key=key)
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
        errors.append("Validation failed. Engine and planner processing skipped.")
        skipped_engines = [
            EngineResult(
                engine="margin-mastery",
                status="SKIPPED",
                payload={"reason": "VALIDATION_FAILED"},
            ),
            EngineResult(
                engine="deadstock",
                status="SKIPPED",
                payload={"reason": "VALIDATION_FAILED"},
            ),
            EngineResult(
                engine="credit-remove",
                status="SKIPPED",
                payload={"reason": "VALIDATION_FAILED"},
            ),
        ]
        planner_result = {
            "status": "SKIPPED",
            "reason": "VALIDATION_FAILED",
            "merged_actions": [e.payload for e in skipped_engines],
        }
        export = _write_export(
            run_id=run_id,
            validation_summary=validation_summary,
            engines=skipped_engines,
            planner_result=planner_result,
            errors=errors,
        )
        return PipelineResult(
            run_id=run_id,
            validation=validation_bundle,
            normalized=normalized,
            engines=skipped_engines,
            planner=planner_result,
            export=export,
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
    planner_url = _resolve_process_url(settings.planner_url, "planner")
    if planner_url:
        try:
            resp = requests.post(
                planner_url, json=planner_payload, timeout=settings.request_timeout_s
            )
            resp.raise_for_status()
            planner_result = resp.json()
            if isinstance(planner_result, dict):
                planner_result.setdefault("endpoint_used", planner_url)
        except Exception as exc:
            planner_result = {"status": "ERROR", "error": str(exc), "endpoint_used": planner_url}
    else:
        planner_result = {
            "status": "SKIPPED",
            "reason": "PLANNER_URL_NOT_SET",
            "environment_variable": "PLANNER_URL",
            "hint": (
                "Set PLANNER_URL to the deployed planner API base URL. "
                "The service must return JSON from GET /health and POST /process."
            ),
            "merged_actions": planner_payload["engine_outputs"],
        }

    export = _write_export(
        run_id=run_id,
        validation_summary=validation_summary,
        engines=engines,
        planner_result=planner_result,
        errors=errors,
    )

    return PipelineResult(
        run_id=run_id,
        validation=validation_bundle,
        normalized=normalized,
        engines=engines,
        planner=planner_result,
        export=export,
    )
