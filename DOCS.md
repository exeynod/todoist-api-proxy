# Todoist API Proxy: Full Documentation

## 1. Overview
`todoist-api-proxy` is a lightweight WSGI REST service that proxies a fixed method catalog to Todoist REST API and returns either:
- `raw` backend payload;
- compact `toon` payload in `{"d": ...}` envelope.

The service keeps compatibility with the original Singularity-style method contract:
- strict input schema validation;
- deterministic error payloads;
- method catalog introspection (`/methods`);
- local pagination fallback in TOON mode for list methods.

Project root:
- `/Users/exy/pet_projects/TodoistAPIProxy`

## 2. High-Level Architecture

### 2.1 Modules
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/app.py`
  - WSGI application and HTTP contract.
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/service.py`
  - Todoist-specific adaptation layer between schema request and API request.
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/methods.py`
  - Fixed catalog of supported methods.
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/schemas.py`
  - Validation rules and request building primitives.
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/client.py`
  - Authenticated HTTP client, timeout, rate limiter, stdlib fallback.
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/toon.py`
  - TOON transformation and compaction rules.
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/__main__.py`
  - Local server entrypoint (`wsgiref.simple_server`).

### 2.2 Request Flow
1. HTTP request enters WSGI app (`app.py`).
2. Route resolves mode (`raw` or `toon`) and method name.
3. JSON body is parsed as object (or empty body -> `{}`).
4. `TodoistClient` is created (token/timeout/rate limit initialized).
5. `execute_method` validates payload by schema and maps to Todoist request.
6. Client executes upstream request with auth headers.
7. Response is returned raw or transformed to TOON.
8. Errors are normalized to JSON error payload contract.

## 3. Runtime and Deployment

### 3.1 Python
- Python requirement: `>=3.9` (`pyproject.toml`).

### 3.2 Local Run
```bash
cd /Users/exy/pet_projects/TodoistAPIProxy
export TODOIST_ACCESS_TOKEN='<your_token>'
PYTHONPATH=src python3 -m todoist_proxy --host 127.0.0.1 --port 8080
```

### 3.3 Docker Run
- Docker image: `/Users/exy/pet_projects/TodoistAPIProxy/Dockerfile`
- Compose stack: `/Users/exy/pet_projects/TodoistAPIProxy/docker-compose.yml`

```bash
cd /Users/exy/pet_projects/TodoistAPIProxy
docker compose up --build -d
```

Compose deployment does not publish host ports.
Service is reachable only from Docker network `todoist_rest_proxy` at `http://todoist-api-proxy:8080`.

Quick check from inside Compose network:
```bash
cd /Users/exy/pet_projects/TodoistAPIProxy
docker compose exec todoist-proxy python -c \
"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/methods', timeout=5).read().decode())"
```

## 4. Environment Variables

### 4.1 Required
- `TODOIST_ACCESS_TOKEN`
  - Primary auth token.

### 4.2 Accepted token alias
- `TODOIST_API_TOKEN`
  - Used if `TODOIST_ACCESS_TOKEN` is absent.

### 4.3 Optional network controls
- `TODOIST_TIMEOUT_SECONDS`
  - Global request timeout.
  - Default: `5`.
  - Invalid/non-positive values fall back to default.
- `TODOIST_RATE_LIMIT_RPS`
  - Token refill rate for limiter.
  - Default: `0.2`.
- `TODOIST_RATE_LIMIT_BURST`
  - Bucket capacity.
  - Default: `2.0`.
- `TODOIST_RATE_LIMIT_STATE_FILE`
  - Shared host-local state file for multi-process coordination.
  - Default: `/tmp/todoist_proxy_rate_limit.json`.

### 4.4 Optional cache controls
- `TODOIST_CACHE_TTL_SECONDS`
  - In-memory response cache TTL for read methods.
  - Default: `15`.
- `TODOIST_CACHE_MAX_SIZE`
  - Max cached entries in-process.
  - Default: `1024`.

## 5. HTTP API Contract

### 5.1 Endpoints
- `GET /methods`
  - Returns method catalog metadata.
- `GET /tasks/today?cursor=<cursor>&limit=<limit>`
  - Convenience TOON endpoint for tasks due today or earlier (overdue + today).
  - Uses Todoist filter: `overdue | today`.
  - Optional query params:
    - `cursor` for upstream cursor pagination;
    - `limit` (`int > 0`) for upstream page size.
- `POST /raw/{method}`
  - Executes method and returns upstream payload as-is.
- `POST /toon/{method}`
  - Executes method and returns TOON envelope.
- `POST /{method}`
  - Same as `POST /toon/{method}` (default mode fallback).

### 5.2 Request Body
- Must be JSON object.
- Empty body is treated as `{}`.
- Invalid JSON or non-object JSON returns validation error.

### 5.3 Per-request token override
When multiple users share the same proxy host, token can be passed per request:
- `Authorization: Bearer <token>` (preferred)
- `X-TODOIST-ACCESS-TOKEN: <token>`

If headers are not provided, service falls back to environment token.

### 5.4 Success Output
- Always JSON.
- Compact serialization (no pretty printing).

### 5.5 Error Output
Format is always:
```json
{"error":{"status":<int>,"message":"<text>"}}
```

## 6. Error Semantics

### 6.1 Validation errors
- HTTP status: `400`
- `error.status`: `3`
- Typical reasons:
  - invalid JSON;
  - non-object input;
  - unknown method;
  - missing required fields;
  - unknown input fields;
  - invalid GET query parameters (for example `limit` on `/tasks/today`).

### 6.2 Missing token
- HTTP status: `401`
- `error.status`: `2`
- Message: `TODOIST_ACCESS_TOKEN is not set`

### 6.3 Upstream API errors
- HTTP status: upstream status if in `400..599`.
- `error.status`: upstream numeric status.
- Message extracted from payload keys:
  - `message`, `error`, `detail`, `title` (including nested `error.message`).

### 6.4 Network/transport errors
- Client raises `ApiError(status=0, ...)`.
- HTTP status returned by app: `502`
- `error.status`: `1`

### 6.5 Unknown route
- HTTP status: `404`
- `error.status`: `404`
- Message: `not found`

## 7. Supported Method Catalog

The service supports exactly these method names:
- `task.list`
- `task.list_by_project`
- `task.list_by_date`
- `task.get`
- `task.create`
- `task.update`
- `task.delete`
- `project.list`
- `project.get`
- `project.create`
- `project.update`
- `project.delete`
- `section.list_by_project`
- `section.get`
- `section.create`
- `section.update`
- `section.delete`
- `checklist.create`
- `checklist.update`
- `checklist.delete`

## 8. Method Mapping to Todoist REST

### 8.1 Tasks
- `task.list`
  - Upstream: `GET /tasks`
  - Optional: `cursor`, `limit`, `page`, `size`.
  - `cursor`/`limit` are forwarded to Todoist.
  - `page`/`size` are local TOON pagination fallback fields.
- `task.list_by_project`
  - Upstream: `GET /tasks?project_id=<project_id>[&cursor=<cursor>&limit=<limit>]`
  - Required: `project_id`
  - Optional: `cursor`, `limit`, `page`, `size`.
- `task.list_by_date`
  - Upstream: `GET /tasks?filter=due on: <date>[&cursor=<cursor>&limit=<limit>]`
  - Required: `date`
  - Optional: `cursor`, `limit`, `page`, `size`.
- `task.get`
  - Upstream: `GET /tasks/{task_id}`
  - Required: `task_id`.
- `task.create`
  - Upstream: `POST /tasks`
  - Required: `name`
  - Optional: `description`, `date`, `startDate`, `endDate`, `priority`, `projectId`, `taskGroupId`
  - Body adaptation:
    - `name` -> `content`
    - `projectId` -> `project_id`
    - `taskGroupId` -> `section_id`
    - `date`/`startDate` -> `due_date` (if date-only) or `due_datetime`
    - `endDate` -> `deadline_date` (first 10 chars for datetime strings)
    - `priority` normalized to Todoist scale (`1..4`).
- `task.update`
  - Upstream: `POST /tasks/{task_id}`
  - Same field mapping as `task.create`.
- `task.delete`
  - Upstream: `DELETE /tasks/{task_id}`.

### 8.2 Projects
- `project.list` -> `GET /projects`
- `project.get` -> `GET /projects/{project_id}`
- `project.create` -> `POST /projects`
- `project.update` -> `POST /projects/{project_id}`
- `project.delete` -> `DELETE /projects/{project_id}`

Project payload note:
- Service only propagates `name` to Todoist body.
- `description` remains accepted by validation contract but is not sent upstream.

### 8.3 Sections
- `section.list_by_project` -> `GET /sections?project_id=<project_id>`
- `section.get` -> `GET /sections/{task_group_id}`
- `section.create` -> `POST /sections`
- `section.update` -> `POST /sections/{task_group_id}`
- `section.delete` -> `DELETE /sections/{task_group_id}`

Section payload note:
- `projectId` is adapted to `project_id`.
- `description` is accepted in input contract but not sent upstream.

### 8.4 Checklist Compatibility (implemented via subtasks)
Todoist REST v2 has no direct checklist entity in this proxy model. Compatibility is implemented with subtasks:
- `checklist.create`
  - `POST /tasks` with:
    - `content = name`
    - `parent_id = task_id`
  - If `isCompleted` is truthy, then `POST /tasks/{new_id}/close`.
- `checklist.update`
  - If `name` provided: `POST /tasks/{checklist_item_id}` with `content`.
  - If `isCompleted` provided:
    - truthy -> `POST /tasks/{id}/close`
    - falsy -> `POST /tasks/{id}/reopen`
  - Final response is fetched with `GET /tasks/{id}`.
- `checklist.delete`
  - `DELETE /tasks/{checklist_item_id}`.

## 9. Input Validation Model

Validation is defined by `MethodSchema` in:
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/schemas.py`

Rules:
1. Payload must be object (`dict`).
2. Required fields must exist and be non-null.
3. Unknown fields are rejected.
4. Request propagation is whitelist-based:
   - only schema-declared `path_params`, `query_params`, `body_params`.
5. Path params are URL-encoded.

## 10. HTTP Client Behavior

Defined in:
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/client.py`

Rules:
- Base URL: `https://api.todoist.com/api/v1`.
- Headers:
  - `Authorization: Bearer <token>`
  - `Accept: application/json`
  - `Content-Type: application/json` only when body exists.
- Payload decoding:
  - try `response.json()`;
  - fallback to `{"message": "<text>"}` when text exists;
  - otherwise `{}`.
- API error extraction checks:
  - `message`, `error`, `detail`, `title`, nested `error.message`.

## 11. Rate Limiting and Safety

Implemented as file-lock token bucket in:
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/client.py`

Behavior:
- Every outbound request calls `rate_limiter.acquire(<token_scope>)`.
- State file is locked with `fcntl.flock(LOCK_EX)`.
- Token refill based on monotonic clock.
- Cross-process coordination on same host via shared state file.
- Buckets are isolated by token fingerprint, so different users do not consume each other's quota.

Default parameters:
- RPS: `0.2`
- Burst: `2.0`
- State file: `/tmp/todoist_proxy_rate_limit.json`

## 12. Response Caching

Implemented in:
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/cache.py`
- integrated in `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/app.py`

Behavior:
- Caches successful GET-method responses (`raw` and `toon` separately).
- Cache key includes:
  - token fingerprint;
  - mode (`raw`/`toon`);
  - method name;
  - normalized request payload JSON.
- Write methods invalidate cache entries only for the same token scope.
- This allows safe multi-user usage on one host without cross-token leakage.

## 13. TOON Transformation Rules

Implemented in:
- `/Users/exy/pet_projects/TodoistAPIProxy/src/todoist_proxy/toon.py`

### 12.1 Envelope and deletes
- Always returns `{"d": ...}`.
- For any `.delete` method: `{"d":{"ok":1}}` regardless of upstream payload.

### 12.2 Datetime conversion
- Datetime-like strings are converted to MSK (`+03:00`) with seconds precision.
- Date-only `YYYY-MM-DD` strings are preserved.
- Naive datetimes are treated as UTC before conversion.

### 12.3 List extraction
- For `.list` methods, extractor searches common container keys:
  - `tasks`, `projects`, `sections`, `data`, `result`, `results`, `items`, `content`, `list`, `rows`
- If no list is found for list method, result is `[]`.

### 12.4 Entity projection
- Task fields projected into compact keys:
  - `n` (name/content/title),
  - `d` (description/note),
  - `s` (date/datetime start-like field),
  - `c` (checklist/subtasks list mapped to checklist projection).
- Project fields:
  - `n`, `d`.
- Section fields:
  - `n`, `d`, `pr`.
- Checklist item fields:
  - `n`.

### 12.5 Meta compaction
- Meta keys are removed from final TOON payload:
  - `i`, `p`, `x`.
- Null/empty strings/empty arrays/empty objects are compacted away.

### 12.6 Local pagination fallback
- Applies only for `.list` methods in TOON mode.
- Requires valid integer `size > 0` and `page >= 1`.
- Uses list slicing semantics:
  - if page is out of range, returns `[]`;
  - otherwise returns requested page slice.

### 12.7 Cursor passthrough
- For `.list` methods, TOON mode preserves upstream `next_cursor` (if present):
  - `{"d":[...], "next_cursor":"..."}`

### 12.8 `task.list_by_date` safety filter
- In TOON mode, `task.list_by_date` additionally keeps only tasks matching requested `date`.
- Tasks without `due` date/time or with a different due date are excluded from `d`.

### 12.9 `/tasks/today` safety filter
- `GET /tasks/today` returns TOON list with tasks that have due date and satisfy `due_date <= today`.
- Tasks without due date are excluded from `d`.

## 14. Requests Fallback Compatibility

If `requests` is installed:
- uses `requests.Session`.

If `requests` is missing:
- uses internal stdlib transport based on `urllib.request`.
- preserves method/url/query/body/headers/timeout behavior.

## 15. Tests

All tests are `unittest` based.

Test modules:
- `/Users/exy/pet_projects/TodoistAPIProxy/tests/test_methods.py`
  - method catalog and request builder.
- `/Users/exy/pet_projects/TodoistAPIProxy/tests/test_client.py`
  - client auth/timeout/error behavior.
- `/Users/exy/pet_projects/TodoistAPIProxy/tests/test_service.py`
  - Todoist adaptation logic and checklist flow.
- `/Users/exy/pet_projects/TodoistAPIProxy/tests/test_toon.py`
  - TOON conversion rules.
- `/Users/exy/pet_projects/TodoistAPIProxy/tests/test_app.py`
  - HTTP app contract.

Run:
```bash
cd /Users/exy/pet_projects/TodoistAPIProxy
python3 -m unittest discover -s tests
```

Live end-to-end run against real Todoist API:
```bash
cd /Users/exy/pet_projects/TodoistAPIProxy
python3 tests/run_live_integration.py --token '<token>' --report-file reports/INTEGRATION_TEST_REPORT.md
```

## 16. Example Calls

Examples below assume local non-Docker run from section `3.2 Local Run`.

### 16.1 Method catalog
```bash
curl -sS http://127.0.0.1:8080/methods
```

### 16.2 Raw task get
```bash
curl -sS -X POST http://127.0.0.1:8080/raw/task.get \
  -H 'content-type: application/json' \
  -d '{"task_id":"12345"}'
```

### 16.3 TOON task list
```bash
curl -sS -X POST http://127.0.0.1:8080/toon/task.list \
  -H 'content-type: application/json' \
  -d '{"page":1,"size":20}'
```

### 16.4 Default TOON mode
```bash
curl -sS -X POST http://127.0.0.1:8080/task.list \
  -H 'content-type: application/json' \
  -d '{"page":1,"size":20}'
```

## 17. Known Behavior Notes
- Task list methods support upstream pagination via `cursor` and `limit` and preserve `next_cursor` in TOON mode.
- `page` and `size` are compatibility fields for local TOON pagination fallback.
- `project.description` and `section.description` are accepted by schema for compatibility but currently not sent to Todoist in adaptation layer.
- The service intentionally uses WSGI stdlib server entrypoint for minimal runtime dependencies.
