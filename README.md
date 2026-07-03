# deploy-simulator

CI/CD deployment simulator.

## Setup

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the app

The **API and frontend run on a single server** — there is no separate frontend process. FastAPI serves the REST API and static files (`App/static/index.html`, `App/static/styles.css`) together.

From the project root, with the virtual environment activated:

```bash
uvicorn App.main:app --reload
```

| What | URL |
|------|-----|
| Frontend UI | http://127.0.0.1:8000/ |
| API docs (Swagger) | http://127.0.0.1:8000/docs |
| Deployments API | http://127.0.0.1:8000/deployments |

**Using the UI**

1. Open http://127.0.0.1:8000/ in your browser.
2. Toggle **Deployment generation** on to call `POST /deployments/start` (adds a deployment every 10 seconds).
3. Toggle it off to call `POST /deployments/stop`.
4. The deployments table refreshes automatically every 5 seconds via `GET /deployments`.

Stop the server with `Ctrl+C` in the terminal.

**Optional flags**

```bash
# Custom host/port
uvicorn App.main:app --host 0.0.0.0 --port 8080 --reload
```

## Data model

`App/models.py` defines a Pydantic `Deployment` record:

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | Unique identifier, >= 1 |
| `service` | literal | `billing-api`, `auth-service`, `notifications`, `frontend-web` |
| `status` | literal | `running`, `success`, `fail`, `rolled-back` (`running` has duration 0 until completed) |
| `duration` | float | Duration in seconds |
| `timestamp` | str | ISO 8601 timestamp |
| `commit_sha` | str | Git commit SHA (7–40 chars) |

## Deployment operations

`App/deployment_ops.py` defines `DeploymentOps`, an in-memory store backed by a `Queue[Deployment]`:

- `add_deployment(deployment)` — add a running deployment (status must be `running`, duration 0)
- `complete_deployment(deployment_id, status, timestamp)` — finalize a running deployment; duration is computed from timestamps
- `read_deployments(service=None, status=None)` — list deployments, optionally filtered
- `read_running_deployments(service=None)` — list in-flight deployments
- `read_deployment(deployment_id)` — fetch one deployment by id

## Metrics

`App/metric.py` defines `MetricOps` for analytics over completed deployments:

- `record_completed(deployment)` — update per-service stats, status counts, and anomaly detection (called by `DeploymentOps` on completion)
- `read_anomalies()` — list anomalous deployments
- `success_rate()` — success count / total × 100
- `p95_duration()` — 95th percentile duration per service

## Dummy data generation

`App/dummy_generator.py` defines `DummyGenerator` for synthetic deployment records:

- `create_dummy_deployment()` — build one random running `Deployment` and return it as a dict
- `start_deployments(ops)` — background thread starts a running deployment every 10 seconds and completes them after a simulated duration
- `stop_deployments()` — stop the background generator

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/deployments` | List deployments; optional `service`, `status` query filters |
| GET | `/deployments/latest` | Latest completed deployments (up to 10) |
| GET | `/deployments/running` | All in-flight running deployments; optional `service` filter |
| GET | `/deployments/{id}` | Get one deployment by id (`id` >= 1) |
| PATCH | `/deployments/{id}` | Complete a running deployment (`status`, `timestamp` in body) |
| GET | `/p95` | P95 duration per service (or `not enough deployments` if fewer than 5) |
| GET | `/anomalies` | List of anomalous deployments |
| GET | `/success-rate` | Success rate percentage `{ "status": "success", "value": 95.0 }` |
| POST | `/deployments/start` | Start dummy deployment generation |
| POST | `/deployments/stop` | Stop dummy deployment generation |

Errors return a structured body: `{ "error", "message", "code", "details" }`.

## Project layout

- `App/` — application code (`main.py`, models, static frontend)
- `App/static/` — `index.html`, `styles.css`
- Root — `README.md`, `.gitignore`, `requirements.txt`
