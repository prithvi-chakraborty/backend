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

The repository defaults are already wired to:

```text
MARGIN_ENGINE_URL=https://margin-mastery.vercel.app
DEADSTOCK_ENGINE_URL=https://deadstock-count.vercel.app
CREDIT_ENGINE_URL=https://credit-compass2.vercel.app
PLANNER_URL=https://profit-pathway-planner2.vercel.app
```

For Vercel deployments the orchestrator automatically uses `/api/health` and
`/api/process`.

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

## Vercel engine API routes

The orchestrator calls `GET /api/health` and `POST /api/process` on each Vercel
engine host. A frontend-only Vercel deploy (Vite UI without the bundled API
routes) returns `404 NOT_FOUND` for those paths.

Each engine project now builds serverless handlers from `api-src/` into
`api/*.cjs` during `npm run build`. After pulling these changes, **redeploy all
four Vercel projects**:

| Vercel project | Local source folder |
|---|---|
| `margin-mastery.vercel.app` | `margin-mastery - Copy/` |
| `deadstock-count.vercel.app` | `deadStock/stock-salvation-system/` |
| `credit-compass2.vercel.app` | `credit-compass2/` |
| `profit-pathway-planner2.vercel.app` | `profit-pathway-planner/profit-pathway-planner/` |

Post-deploy smoke test:

```bash
curl https://margin-mastery.vercel.app/api/health
curl -X POST -H "Content-Type: application/json" -d "{\"dataset\":{\"records\":[]}}" https://margin-mastery.vercel.app/api/process
```

Repeat for the other three hosts. Each `/api/health` should return HTTP 200 JSON
with an `engine` field, and `/api/process` should return HTTP 200 (not 404 or
`FUNCTION_INVOCATION_FAILED`).
