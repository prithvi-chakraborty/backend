import os
import re
from urllib.parse import urlparse, urlunparse

import requests
import streamlit as st


st.set_page_config(page_title="Profit Pathway Planner", layout="wide")

st.title("Profit Pathway Planner - Automation Console")
st.caption("Upload three CSVs, validate, run engines, and export in one flow.")


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
    st.error(
        "Missing/invalid FASTAPI_URL. Set `FASTAPI_URL` (env var or Streamlit secret) "
        "to your orchestrator base URL (example: https://<service>.onrender.com)."
    )
    st.stop()


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


if start:
    if not (margin_file and deadstock_file and credit_file):
        st.error("Please upload all three CSV files before starting.")
    else:
        status.info("Validating files...")
        progress.progress(10)

        try:
            result = _call_fastapi(margin_file, deadstock_file, credit_file)
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
