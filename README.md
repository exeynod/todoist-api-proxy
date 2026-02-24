# Todoist API Proxy

REST proxy service for Todoist API with two response modes:
- `raw`: backend JSON as-is
- `toon`: compact TOON JSON (`{"d": ...}`)

Current upstream API base:
- `https://api.todoist.com/api/v1`

## Requirements
- Python `>=3.9`

Optional environment variables:
- `TODOIST_TIMEOUT_SECONDS` (default: `5`)
- `TODOIST_RATE_LIMIT_RPS` (default: `0.2`)
- `TODOIST_RATE_LIMIT_BURST` (default: `2.0`)
- `TODOIST_RATE_LIMIT_STATE_FILE` (default: `/tmp/todoist_proxy_rate_limit.json`)
- `TODOIST_CACHE_TTL_SECONDS` (default: `15`)
- `TODOIST_CACHE_MAX_SIZE` (default: `1024`)

API call logs:
- Outbound Todoist API calls are appended to `/tmp/logs_YYYYMMDD.log` (one file per day, JSONL).
- Each line includes upstream call data and proxy context:
  - `proxy_mode` (`raw` or `toon`);
  - `proxy_path` (for example `/raw/task.get`, `/toon/task.list`, `/task.list`, `/tasks/today`);
  - `proxy_method` (resolved catalog method, for example `task.get`).

## Run
```bash
PYTHONPATH=src python3 -m todoist_proxy --host 127.0.0.1 --port 8080
```

## Run with Docker Compose
```bash
docker compose up --build -d
```

Compose deployment does not publish host ports.  
Service is reachable only from Docker network `todoist_rest_proxy` at `http://todoist-api-proxy:8080`.

Quick check from inside Compose network:
```bash
docker compose exec todoist-proxy python -c \
"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/methods', timeout=5).read().decode())"
```

## Endpoints
- `GET /methods`
- `GET /tasks/today?cursor=<cursor>&limit=<limit>` (TOON list of tasks due today or overdue)
- `POST /raw/{method}`
- `POST /toon/{method}`
- `POST /{method}` (default `toon` mode)

`POST` body must be a JSON object. Empty body means `{}`.

Every Todoist request must include token in headers:
- `Authorization: Bearer <token>` (preferred)
- `X-TODOIST-ACCESS-TOKEN: <token>`

Task-specific notes:
- `task.close` is supported (`POST /raw/task.close`, `POST /toon/task.close`, `POST /task.close`).
- `task.create`/`task.update` accept section aliases: `taskGroupId`, `sectionId`, `section_id`.
- `task.create`/`task.update` accept priority in `priority` and compact alias `p`.
- Priority shorthand: `P1` is highest, `P4` is lowest.
- `task.create`/`task.update` accept labels in `labels` and compact alias `l`.
- TOON entity payloads include `i` (entity id) for tasks/projects/sections/checklist items.
- TOON task payload may include `l` (labels list).
- TOON task payload may include `p` (priority `1..4`).
- TOON task payload may include `tg` (section id).

## Examples
Examples below assume local non-Docker run from the `Run` section.

```bash
curl -sS http://127.0.0.1:8080/methods

curl -sS -X POST http://127.0.0.1:8080/raw/task.get \
  -H "Authorization: Bearer <token>" \
  -H 'content-type: application/json' \
  -d '{"task_id":"12345"}'

curl -sS -X POST http://127.0.0.1:8080/toon/task.list \
  -H "Authorization: Bearer <token>" \
  -H 'content-type: application/json' \
  -d '{"page":1,"size":20}'

curl -sS "http://127.0.0.1:8080/tasks/today?limit=50" \
  -H "Authorization: Bearer <token>"

curl -sS -X POST http://127.0.0.1:8080/toon/task.create \
  -H "Authorization: Bearer <token>" \
  -H "content-type: application/json" \
  -d '{"name":"Оплатить счет","description":"До конца дня","date":"2026-02-21"}'

curl -sS -X POST http://127.0.0.1:8080/toon/task.close \
  -H "Authorization: Bearer <token>" \
  -H "content-type: application/json" \
  -d '{"task_id":"12345"}'
```

## Live Integration Suite
Run full live integration checks (all methods + raw/toon/default routes + multi-token checks):

```bash
python3 tests/run_live_integration.py \
  --token '<your_token>' \
  --report-file reports/INTEGRATION_TEST_REPORT.md
```
