# Profit Pathway Orchestrator

FastAPI orchestrator deployed on Render and used by the Streamlit and Vercel
frontends.

## Render start command

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Render environment variables

Configure these on the **backend/orchestrator Render service**, not only in
`CORS_ORIGINS`:

```text
MARGIN_ENGINE_URL=https://your-margin-api
DEADSTOCK_ENGINE_URL=https://your-deadstock-api
CREDIT_ENGINE_URL=https://your-credit-api
PLANNER_URL=https://your-planner-api
CORS_ORIGINS=https://your-vercel-ui,https://your-streamlit-ui
```

Each engine URL may be either a base URL or a `/process` URL. Each deployed
engine must return JSON from `GET /health` and `POST /process`.

If the four engine variables cannot be configured individually, set:

```text
ENGINE_URLS=https://margin-api,https://deadstock-api,https://credit-api,https://planner-api
```

The orchestrator identifies each service through its `/health` response.

## Deployment checks

- `GET /health` reports whether all engine endpoints resolve.
- `GET /configuration` reports the resolved endpoint and environment source.
- `GET /` confirms the orchestrator itself is running.
