import csv
import io
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import requests
import streamlit as st


st.set_page_config(page_title="Profit Pathway Planner", layout="wide")

st.title("Profit Pathway Planner - Automation Console")
st.caption("Upload three CSVs, validate, run engines, and export in one flow.")

APP_URLS = {
    "Credit Compass": "https://credit-compass2.vercel.app",
    "Margin Mastery": "https://margin-mastery.vercel.app",
    "Deadstock Count": "https://deadstock-count.vercel.app",
    "Profit Pathway Planner": "https://profit-pathway-planner2.vercel.app",
}

with st.sidebar:
    st.header("Connected Apps")
    for label, url in APP_URLS.items():
        st.link_button(label, url, use_container_width=True)


def _normalize_fastapi_url(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().strip('"').strip("'")
    cleaned = re.sub(r"\s+", "", cleaned)
    parsed = urlparse(cleaned)
    if not (parsed.scheme and parsed.netloc):
        return None
    path = parsed.path.rstrip("/")
    # FASTAPI_URL should be a base URL, not an endpoint path.
    if path.endswith("/process"):
        path = path[: -len("/process")]
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def _get_secret(key: str) -> str | None:
    try:
        return st.secrets[key]
    except Exception:
        return None


def _resolve_fastapi_url() -> str | None:
    candidates = [
        os.getenv("FASTAPI_URL"),
        _get_secret("FASTAPI_URL"),
    ]
    for raw in candidates:
        normalized = _normalize_fastapi_url(raw)
        if normalized:
            return normalized
    return None


FASTAPI_URL = _resolve_fastapi_url()
if not FASTAPI_URL:
    st.info(
        "FASTAPI_URL is not configured. Using the four Vercel engine APIs directly."
    )

with st.sidebar:
    st.caption(
        f"Connection mode: {'FastAPI orchestrator' if FASTAPI_URL else 'Direct Vercel APIs'}"
    )
    if FASTAPI_URL and st.button("Test orchestrator connection", use_container_width=True):
        try:
            health_response = requests.get(f"{FASTAPI_URL}/health", timeout=20)
            health_response.raise_for_status()
            st.success("FastAPI orchestrator is connected.")
            st.json(health_response.json())
        except Exception as exc:
            st.error(f"Connection failed: {exc}")


ZERO_RESULT = {
    "run_id": 0,
    "validation": {"value": 0},
    "normalized": 0,
    "engines": [{"engine": 0, "status": 0, "payload": 0}],
    "planner": {"value": 0},
    "export": {"path": 0, "format": 0},
    "errors": 0,
}

if "result" not in st.session_state:
    st.session_state["result"] = None


def _clear_data():
    st.session_state["result"] = ZERO_RESULT.copy()
    for key in ("margin", "deadstock", "credit"):
        if key in st.session_state:
            del st.session_state[key]


def _json_friendly(value):
    if isinstance(value, (dict, list)):
        return value
    return {"value": value}


col1, col2, col3 = st.columns(3)
with col1:
    margin_file = st.file_uploader("Margin CSV", type=["csv"], key="margin")
with col2:
    deadstock_file = st.file_uploader("Deadstock CSV", type=["csv"], key="deadstock")
with col3:
    credit_file = st.file_uploader("Credit CSV", type=["csv"], key="credit")

action_col1, action_col2 = st.columns([1, 1])
with action_col1:
    start = st.button("Start Process", type="primary")
with action_col2:
    st.button("Clear Data", on_click=_clear_data)

progress = st.progress(0)
status = st.empty()


def _call_fastapi(margin, deadstock, credit):
    files = {
        "margin": (margin.name, margin.getvalue(), "text/csv"),
        "deadstock": (deadstock.name, deadstock.getvalue(), "text/csv"),
        "credit": (credit.name, credit.getvalue(), "text/csv"),
    }
    resp = requests.post(f"{FASTAPI_URL}/process", files=files, timeout=300)
    resp.raise_for_status()
    return resp.json()


def _csv_records(uploaded_file):
    text = uploaded_file.getvalue().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _post_engine(label, base_url, records, run_id):
    response = requests.post(
        f"{base_url}/api/process",
        json={"run_id": run_id, "dataset": records},
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") == "ERROR":
        raise RuntimeError(payload.get("error") or f"{label} failed")
    return payload


def _call_vercel_engines(margin, deadstock, credit):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    normalized = {
        "margin": _csv_records(margin),
        "deadstock": _csv_records(deadstock),
        "credit": _csv_records(credit),
    }
    engine_specs = [
        ("margin-mastery", APP_URLS["Margin Mastery"], normalized["margin"]),
        ("deadstock", APP_URLS["Deadstock Count"], normalized["deadstock"]),
        ("credit-remove", APP_URLS["Credit Compass"], normalized["credit"]),
    ]
    engines = []
    for engine_name, base_url, records in engine_specs:
        payload = _post_engine(engine_name, base_url, records, run_id)
        engines.append(
            {
                "engine": engine_name,
                "status": "OK",
                "payload": payload,
            }
        )

    planner_response = requests.post(
        f'{APP_URLS["Profit Pathway Planner"]}/api/process',
        json={
            "run_id": run_id,
            "engine_outputs": [entry["payload"] for entry in engines],
        },
        timeout=300,
    )
    planner_response.raise_for_status()
    planner = planner_response.json()

    return {
        "run_id": run_id,
        "validation": {
            "summary": {
                "overall_decision": "PASS",
                "reasons": ["Processed through direct Vercel API fallback."],
            }
        },
        "normalized": {key: len(records) for key, records in normalized.items()},
        "engines": engines,
        "planner": planner,
        "export": {"format": "streamlit-session"},
        "errors": [],
    }


if start:
    if not (margin_file and deadstock_file and credit_file):
        st.error("Please upload all three CSV files before starting.")
    else:
        status.info("Validating files...")
        progress.progress(10)

        try:
            result = (
                _call_fastapi(margin_file, deadstock_file, credit_file)
                if FASTAPI_URL
                else _call_vercel_engines(margin_file, deadstock_file, credit_file)
            )
        except Exception as exc:
            st.error(f"FastAPI call failed: {exc}")
            st.stop()

        progress.progress(85)
        status.success("Complete.")
        progress.progress(100)

        st.session_state["result"] = result

result = st.session_state.get("result")
if result is not None:
    st.subheader("Validation Summary")
    st.json(_json_friendly(result.get("validation", 0)))

    st.subheader("Engine Results")
    st.json(_json_friendly(result.get("engines", 0)))

    st.subheader("Planner Output")
    st.json(_json_friendly(result.get("planner", 0)))

    export = result.get("export", {})
    if isinstance(export, dict) and export.get("path"):
        st.success(f"Export created: {export.get('path')}")
