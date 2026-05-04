# Streamlit UI (separate deploy)

This folder contains a Streamlit UI that talks to the FastAPI orchestrator **over HTTP**.

## Environment variables

- `FASTAPI_URL`: base URL of the orchestrator (example: `https://<orchestrator>.onrender.com`)

## Render settings

- Root directory: `backend/streamlit ui`
- Build command: `pip install -r requirements.txt`
- Start command:
  - `streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true`
