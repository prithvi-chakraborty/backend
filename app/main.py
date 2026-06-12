from __future__ import annotations

import json
import os
from typing import Dict, Tuple, Any

from fastapi import FastAPI, File, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response

from .config import service_discovery_candidates, settings
from .processor import engine_configuration, process_pipeline

LATEST_RESULT: dict[str, Any] | None = None


def _load_latest_export() -> dict[str, Any] | None:
    export_dir = settings.export_dir
    if not export_dir or not os.path.isdir(export_dir):
        return None

    candidates = [
        os.path.join(export_dir, name)
        for name in os.listdir(export_dir)
        if name.startswith("profit-pathway-export-") and name.endswith(".json")
    ]
    if not candidates:
        return None

    latest = max(candidates, key=lambda p: os.path.getmtime(p))
    try:
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _get_latest_result() -> dict[str, Any] | None:
    if LATEST_RESULT is not None:
        return LATEST_RESULT
    return _load_latest_export()


def _find_engine_payload(latest: dict[str, Any], engine: str) -> dict[str, Any] | None:
    engines = latest.get("engines") or []
    for entry in engines:
        name = entry.get("engine")
        if name == engine:
            return entry
    for entry in engines:
        payload = entry.get("payload") or {}
        payload_engine = payload.get("engine")
        payload_name = payload.get("engine_name")
        if payload_engine == engine or payload_name == engine:
            return entry
    return None


def _status_http_code(status: str) -> int:
    if status == "SKIPPED":
        return 424
    if status == "ERROR":
        return 502
    return 500


def _status_message(status: str, payload: Any, label: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("reason") or payload.get("message")
        if detail:
            return str(detail)
    if status == "SKIPPED":
        return f"{label} processing was skipped"
    if status == "ERROR":
        return f"{label} processing failed"
    return f"{label} status is {status}"

app = FastAPI(title="Profit Pathway Orchestrator", version="1.0.0")

def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS")
    dev_defaults = {
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "https://credit-compass2.vercel.app",
        "https://margin-mastery.vercel.app",
        "https://deadstock-count.vercel.app",
        "https://profit-pathway-planner2.vercel.app",
    }
    if raw:
        origins = {o.strip() for o in raw.split(",") if o.strip()}
        if "*" in origins:
            return ["*"]
        return sorted(origins | dev_defaults)
    # Allow all origins in local dev to avoid port/host mismatches.
    return sorted(dev_defaults | {"*"})


_ALLOWED_ORIGINS = _cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _cors_fallback(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method == "OPTIONS"
        and origin
        and request.headers.get("access-control-request-method")
    ):
        headers = {
            "Access-Control-Allow-Origin": origin
            if origin in _ALLOWED_ORIGINS or "*" in _ALLOWED_ORIGINS
            else "",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": request.headers.get(
                "access-control-request-headers", "*"
            ),
            "Vary": "Origin",
        }
        return Response(status_code=200, headers=headers)

    response = await call_next(request)
    if origin and (origin in _ALLOWED_ORIGINS or "*" in _ALLOWED_ORIGINS):
        response.headers.setdefault("Access-Control-Allow-Origin", origin)
        response.headers.setdefault("Vary", "Origin")
    return response


@app.get("/health")
def health() -> dict:
    configuration = engine_configuration()
    return {
        "status": "ok",
        "engine_urls": {
            name: details["resolved"]
            for name, details in configuration.items()
        },
        "configuration_sources": {
            name: details["source"]
            for name, details in configuration.items()
        },
        "discovery_candidate_count": len(service_discovery_candidates()),
    }


@app.get("/configuration")
def configuration() -> dict:
    return {
        "status": "ok",
        "engines": engine_configuration(),
        "accepted_environment_variables": {
            "margin": "MARGIN_ENGINE_URL",
            "deadstock": "DEADSTOCK_ENGINE_URL",
            "credit": "CREDIT_ENGINE_URL",
            "planner": "PLANNER_URL",
            "fallback_list": "ENGINE_URLS",
        },
    }


@app.get("/")
def root() -> dict:
    return {
        "service": "Profit Pathway Orchestrator",
        "status": "ok",
        "health": "/health",
        "configuration": "/configuration",
        "process": "/process",
    }


@app.post("/process")
async def process(
    margin: UploadFile = File(...),
    deadstock: UploadFile = File(...),
    credit: UploadFile = File(...),
):
    files: Dict[str, Tuple[str, bytes]] = {}
    for key, up in [("margin", margin), ("deadstock", deadstock), ("credit", credit)]:
        files[key] = (up.filename or f"{key}.csv", await up.read())

    result = process_pipeline(files)
    data = result.dict()
    global LATEST_RESULT
    LATEST_RESULT = data
    return data


@app.get("/latest")
def latest() -> dict:
    latest_result = _get_latest_result()
    if not latest_result:
        return {"status": "missing", "message": "No recent run found"}
    return latest_result


@app.get("/latest/engine/{engine}")
def latest_engine(engine: str) -> dict:
    latest_result = _get_latest_result()
    if not latest_result:
        return {"status": "missing", "message": "No recent run found"}
    entry = _find_engine_payload(latest_result, engine)
    if not entry:
        return {"status": "missing", "message": f"No engine output for {engine}"}
    payload = entry.get("payload") or {}
    status = str(entry.get("status") or "UNKNOWN")
    body = {
        "engine": entry.get("engine"),
        "status": status,
        "payload": payload,
    }
    if status != "OK":
        body["message"] = _status_message(status, payload, str(entry.get("engine") or engine))
        return JSONResponse(status_code=_status_http_code(status), content=body)
    return body


@app.get("/latest/planner")
def latest_planner() -> dict:
    latest_result = _get_latest_result()
    if not latest_result:
        return {"status": "missing", "message": "No recent run found"}
    planner = latest_result.get("planner")
    if not planner:
        return {"status": "missing", "message": "No planner output found"}
    status = planner.get("status") if isinstance(planner, dict) else None
    if isinstance(status, str) and status != "OK":
        body = dict(planner)
        body["message"] = _status_message(status, planner, "planner")
        return JSONResponse(status_code=_status_http_code(status), content=body)
    return planner
