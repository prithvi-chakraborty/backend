# Streamlit UI (separate deploy)

This folder contains a Streamlit UI that talks to the FastAPI orchestrator **over HTTP**.

## Environment variables

- `FASTAPI_URL`: base URL of the orchestrator (example: `https://<orchestrator>.onrender.com`)

When `FASTAPI_URL` is configured it must point to the deployed FastAPI
orchestrator, not to one of the Vercel frontend URLs. If it is omitted,
Streamlit automatically calls the four configured Vercel `/api/process`
endpoints directly.

## Render settings

- Root directory: `streamlit_ui`
- Build command: `pip install -r requirements.txt`
- Start command:
  - `streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true`
