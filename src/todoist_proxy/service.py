from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from todoist_proxy.methods import get_schema
from todoist_proxy.schemas import JsonDict, RequestSpec

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def execute_method(client: Any, method_name: str, payload: dict[str, Any] | None) -> Any:
    schema = get_schema(method_name)
    data = schema.validate_input(payload)

    if method_name == "checklist.create":
        return _execute_checklist_create(client, data)
    if method_name == "checklist.update":
        return _execute_checklist_update(client, data)

    spec = schema.to_request(data)
    spec = _adapt_for_todoist(method_name, spec, data)
    return client.request(spec)


def _adapt_for_todoist(method_name: str, spec: RequestSpec, data: JsonDict) -> RequestSpec:
    if method_name == "task.list_by_date":
        query = dict(spec.query)
        query["filter"] = f"due on: {data['date']}"
        return replace(spec, query=query)

    if method_name in {"task.create", "task.update"}:
        return replace(spec, body=_task_payload_to_todoist(spec.body))

    if method_name in {"project.create", "project.update"}:
        body = {"name": spec.body["name"]} if "name" in spec.body else {}
        return replace(spec, body=body)

    if method_name in {"section.create", "section.update"}:
        body: JsonDict = {}
        if "name" in spec.body:
            body["name"] = spec.body["name"]
        if "projectId" in spec.body:
            body["project_id"] = spec.body["projectId"]
        return replace(spec, body=body)

    return spec


def _task_payload_to_todoist(payload: JsonDict) -> JsonDict:
    body: JsonDict = {}

    if "name" in payload:
        body["content"] = payload["name"]
    if "description" in payload:
        body["description"] = payload["description"]
    if "projectId" in payload:
        body["project_id"] = payload["projectId"]
    section_id = _task_section_id(payload)
    if section_id is not None:
        body["section_id"] = section_id
    labels_value = payload.get("labels")
    if labels_value is None and "l" in payload:
        labels_value = payload.get("l")
    labels = _normalize_labels(labels_value)
    if labels:
        body["labels"] = labels
    elif _is_explicit_empty_labels(labels_value):
        body["labels"] = []

    priority: int | None = None
    if payload.get("p") is not None:
        priority = _normalize_priority(payload.get("p"), assume_toon_scale=True)
    if priority is None:
        priority = _normalize_priority(payload.get("priority"), assume_toon_scale=False)
    if priority is not None:
        body["priority"] = priority

    start_date = payload.get("startDate")
    if not isinstance(start_date, str):
        start_date = payload.get("date")
    if isinstance(start_date, str):
        if _DATE_ONLY_RE.fullmatch(start_date):
            body["due_date"] = start_date
        else:
            body["due_datetime"] = start_date

    end_date = payload.get("endDate")
    if isinstance(end_date, str):
        body["deadline_date"] = end_date[:10] if len(end_date) >= 10 else end_date

    return body


def _task_section_id(payload: JsonDict) -> str | None:
    for key in ("taskGroupId", "sectionId", "section_id"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_labels(value: Any) -> list[str]:
    if isinstance(value, str):
        label = value.strip()
        return [label] if label else []

    if isinstance(value, list):
        labels: list[str] = []
        for item in value:
            if isinstance(item, str):
                label = item.strip()
                if label:
                    labels.append(label)
                continue
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    label = name.strip()
                    if label:
                        labels.append(label)
        return labels

    return []


def _is_explicit_empty_labels(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 0


def _normalize_priority(value: Any, *, assume_toon_scale: bool = False) -> int | None:
    named = {
        "natural": 1,
        "normal": 1,
        "low": 1,
        "medium": 2,
        "high": 3,
        "urgent": 4,
    }

    api_priority: int | None = None
    toon_priority: int | None = None

    if isinstance(value, int):
        if value in (1, 2, 3, 4):
            if assume_toon_scale:
                toon_priority = value
            else:
                api_priority = value
        elif value == 0:
            api_priority = 1
        else:
            return None
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        if text in named:
            api_priority = named[text]
        if text.startswith("p") and len(text) == 2 and text[1].isdigit():
            level = int(text[1])
            if 1 <= level <= 4:
                toon_priority = level
        elif text.isdigit():
            return _normalize_priority(int(text), assume_toon_scale=assume_toon_scale)

    if toon_priority is not None:
        # Todoist clients use P1..P4, API stores 4..1.
        return 5 - toon_priority

    return api_priority


def _execute_checklist_create(client: Any, payload: JsonDict) -> Any:
    create_spec = RequestSpec(
        method="POST",
        path="/tasks",
        query={},
        body={
            "content": payload["name"],
            "parent_id": payload["task_id"],
        },
    )
    created = client.request(create_spec)

    if _as_bool(payload.get("isCompleted")):
        checklist_id = _extract_entity_id(created)
        if checklist_id:
            _set_task_completion(client, checklist_id, True)

    return created


def _execute_checklist_update(client: Any, payload: JsonDict) -> Any:
    checklist_id = str(payload["checklist_item_id"])

    if "name" in payload and payload["name"] is not None:
        update_spec = RequestSpec(
            method="POST",
            path=f"/tasks/{checklist_id}",
            query={},
            body={"content": payload["name"]},
        )
        client.request(update_spec)

    if "isCompleted" in payload and payload["isCompleted"] is not None:
        _set_task_completion(client, checklist_id, _as_bool(payload.get("isCompleted")))

    get_spec = RequestSpec(
        method="GET",
        path=f"/tasks/{checklist_id}",
        query={},
        body={},
    )
    return client.request(get_spec)


def _set_task_completion(client: Any, task_id: str, is_completed: bool) -> None:
    endpoint = "close" if is_completed else "reopen"
    spec = RequestSpec(
        method="POST",
        path=f"/tasks/{task_id}/{endpoint}",
        query={},
        body={},
    )
    client.request(spec)


def _extract_entity_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("id")
        if value is not None:
            return str(value)
    return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False
