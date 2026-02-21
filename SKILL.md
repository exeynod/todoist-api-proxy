---
name: todoist-api-proxy
description: Use Todoist API Proxy REST methods for any request about Todoist tasks, projects, sections, and checklist items.
metadata: {"openclaw":{"skillKey":"todoistApiProxy","requires":{"env":["TODOIST_PROXY_BASE_URL","TODOIST_USER_TOKEN"]},"primaryEnv":"TODOIST_USER_TOKEN"}}
---

# Todoist API Proxy

Use this skill for any user request about Todoist data (tasks, projects, sections, checklist items).

## Trigger Rules

- Always use this skill if user intent is about Todoist tasks/projects/sections/checklists.
- Typical trigger phrases:
  - "Сколько задач на сегодня"
  - "Покажи задачи по работе"
  - "Создай задачу"
  - "Обнови проект"
  - "Удали чеклист"

## Required Environment

- `TODOIST_PROXY_BASE_URL`: proxy base URL (example: `http://127.0.0.1:8080`).
- `TODOIST_USER_TOKEN`: per-user Todoist token used in `Authorization: Bearer <token>`.

## REST Call Rules

- Endpoints:
  - `GET /methods`
  - `GET /tasks/today?cursor=<cursor>&limit=<limit>` (TOON convenience endpoint: overdue + today)
  - `POST /raw/{method}`
  - `POST /toon/{method}`
  - `POST /{method}` (default TOON mode)
- Use `curl` only for Todoist proxy calls. Do not use Python or other HTTP clients by default.
- Default to TOON mode unless user explicitly asks for raw upstream JSON.
- Headers:
  - `Authorization: Bearer ${TODOIST_USER_TOKEN}` (preferred)
  - `X-TODOIST-ACCESS-TOKEN: ${TODOIST_USER_TOKEN}` (fallback)
  - `Content-Type: application/json` for `POST`
- Body must be a JSON object; send `{}` when no inputs are needed.
- Before uncertain calls, use `GET /methods` to confirm actual required/optional fields.

## Behavior Policy

- For Todoist requests, use only this proxy via `${TODOIST_PROXY_BASE_URL}`.
- Never call external APIs directly (including `api.todoist.com`) and never switch to other providers/services.
- Never invent endpoints, methods, or fields not present in this skill and `/methods`.
- Use only currently supported functionality from the method list below.
- By default, execute at most one proxy request per user task.
- If the task requires 2+ requests (for example id resolution + target action), ask user for explicit approval before making additional requests.
- If implementation would require Python (or any non-`curl` transport), ask user for explicit approval first.
- If request is outside supported functionality, state that it is not supported and do not improvise workaround logic.
- Do not attempt to fix infrastructure/network/auth issues on your own:
  - do not restart services/containers,
  - do not change environment variables,
  - do not suggest hidden fallback endpoints.
- If proxy call fails (4xx/5xx/transport), return the proxy error as-is and ask user for the minimal next action (for example, provide valid token, method inputs, or service availability).
- If required method inputs are missing, ask only for missing required fields.
- Use the minimal required sequence of method calls (single-call if enough; multi-call only when needed).
- If user gives a project/section/task name instead of id, first resolve id via supported list/get methods, then call the target method.

## Method Selection

Choose the minimal required sequence of these methods based on user intent:

| Method | When to call | Required input |
| --- | --- | --- |
| `task.list` | List tasks. | none |
| `task.list_by_project` | List tasks by project. | `project_id` |
| `task.list_by_date` | List tasks by date. | `date` |
| `task.get` | Get task details. | `task_id` |
| `task.create` | Create task. | `name` |
| `task.update` | Update task. | `task_id` |
| `task.delete` | Delete task. | `task_id` |
| `project.list` | List projects. | none |
| `project.get` | Get project details. | `project_id` |
| `project.create` | Create project. | `name` |
| `project.update` | Update project. | `project_id` |
| `project.delete` | Delete project. | `project_id` |
| `section.list_by_project` | List sections by project. | `project_id` |
| `section.get` | Get section details. | `task_group_id` |
| `section.create` | Create section. | `name`, `projectId` |
| `section.update` | Update section. | `task_group_id` |
| `section.delete` | Delete section. | `task_group_id` |
| `checklist.create` | Create checklist item. | `task_id`, `name` |
| `checklist.update` | Update checklist item. | `task_id`, `checklist_item_id` |
| `checklist.delete` | Delete checklist item. | `task_id`, `checklist_item_id` |

## Useful Optional Inputs

- Pagination for task list methods: `cursor`, `limit` (upstream) and `page`, `size` (local fallback in TOON mode).
- Task creation/update optional fields: `description`, `date`, `startDate`, `endDate`, `priority`, `projectId`, `taskGroupId`.
- Checklist completion toggle: `isCompleted` (`true`/`false`).

## Response Rules

- TOON success envelope: `{"d": ...}`.
- TOON list methods: `d` is an array.
- TOON list methods may include `next_cursor` when upstream returns it.
- TOON get/create/update methods: `d` is an object.
- TOON delete methods: `{"d":{"ok":1}}`.
- Error payload: `{"error":{"status":<int>,"message":"<text>"}}`.

## Practical Examples

1. "Сколько задач на сегодня":
   - Call `POST ${TODOIST_PROXY_BASE_URL}/toon/task.list_by_date` with `{"date":"YYYY-MM-DD"}`.
   - Read `response.d` array and return count.

2. "Покажи задачи по работе":
   - Call `POST ${TODOIST_PROXY_BASE_URL}/toon/project.list`, resolve project id for "Работа".
   - Call `POST ${TODOIST_PROXY_BASE_URL}/toon/task.list_by_project` with `{"project_id":"<id>"}`.
   - Return concise list from `response.d`.

3. "Создай задачу":
   - Call `POST ${TODOIST_PROXY_BASE_URL}/toon/task.create` with `{"name":"..."}`.
   - Read created task from `response.d`.

## Curl Template
```bash
curl -sS -X POST "${TODOIST_PROXY_BASE_URL}/toon/<method>" \
  -H "Authorization: Bearer ${TODOIST_USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '<json-object-body>'
```

## How To Read Result

- Success: `response.d`.
- Error: `response.error.status` and `response.error.message`.
