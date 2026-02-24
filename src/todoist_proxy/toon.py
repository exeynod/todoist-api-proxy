from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

MSK = timezone(timedelta(hours=3))
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_KEY_HINTS = {
    "startDate",
    "endDate",
    "completedDate",
    "due_datetime",
    "dueDate",
    "datetime",
    "deadline_date",
}
_CHECKLIST_KEYS = (
    "checklist",
    "checklistItems",
    "checkList",
    "checklistItemList",
    "subtasks",
    "children",
)
_LIST_CONTAINER_KEYS = (
    "tasks",
    "projects",
    "sections",
    "data",
    "result",
    "results",
    "items",
    "content",
    "list",
    "rows",
)
_PAGINATION_CURSOR_KEYS = ("next_cursor", "nextCursor")
_NOTE_POINTER_RE = re.compile(r"^N-[A-Za-z0-9-]+$")
_META_FIELDS = {"p", "x"}


def to_toon_response(
    method_name: str,
    raw_payload: Any,
    request_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if method_name.endswith(".delete") or method_name == "task.close":
        return {"d": {"ok": 1}}

    prepared = convert_datetimes_to_msk(raw_payload)
    next_cursor = _extract_next_cursor(prepared) if ".list" in method_name else None
    prefer_list = ".list" in method_name
    payload = _extract_data(prepared, prefer_list=prefer_list)
    if method_name == "task.list_by_date":
        payload = _filter_task_list_by_date(payload, request_input)
    if method_name == "task.list_today":
        payload = _filter_task_list_due_on_or_before(payload, request_input)

    if method_name.startswith("task."):
        data = _normalize_collection(payload, _task_to_toon)
    elif method_name.startswith("project."):
        data = _normalize_collection(payload, _project_to_toon)
    elif method_name.startswith("section."):
        data = _normalize_collection(payload, _section_to_toon)
    elif method_name.startswith("checklist."):
        data = _normalize_collection(payload, _checklist_item_to_toon)
    else:
        data = payload

    data = _apply_local_pagination(method_name, data, request_input)
    data = _strip_meta_fields(data)

    result: dict[str, Any] = {"d": data}
    if next_cursor is not None:
        result["next_cursor"] = next_cursor
    return result


def convert_datetimes_to_msk(payload: Any, key_hint: str | None = None) -> Any:
    if isinstance(payload, dict):
        return {k: convert_datetimes_to_msk(v, key_hint=k) for k, v in payload.items()}
    if isinstance(payload, list):
        return [convert_datetimes_to_msk(item, key_hint=key_hint) for item in payload]
    if isinstance(payload, str) and _should_convert_datetime(key_hint, payload):
        return _convert_one_datetime(payload)
    return payload


def _should_convert_datetime(key_hint: str | None, value: str) -> bool:
    if _DATE_ONLY_RE.fullmatch(value):
        return False

    if "T" in value:
        return True

    if not key_hint:
        return False

    if key_hint.endswith("At") or key_hint in _DATETIME_KEY_HINTS:
        return ":" in value

    return False


def _convert_one_datetime(value: str) -> str:
    prepared = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(prepared)
    except ValueError:
        return value

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(MSK).isoformat(timespec="seconds")


def _extract_data(payload: Any, prefer_list: bool = False) -> Any:
    if not isinstance(payload, dict):
        return payload

    if prefer_list:
        list_payload = _extract_list_payload(payload)
        if list_payload is not None:
            return list_payload
        return []

    keys = ("task", "project", "section", "comment", "data", "result", "items", "content")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            return value

    return payload


def _extract_list_payload(payload: Any, max_depth: int = 3) -> list[Any] | None:
    queue: list[tuple[Any, int]] = [(payload, 0)]
    seen: set[int] = set()

    while queue:
        current, depth = queue.pop(0)

        if isinstance(current, list):
            return current
        if not isinstance(current, dict):
            continue

        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        for key in _LIST_CONTAINER_KEYS:
            value = current.get(key)
            if isinstance(value, list):
                return value

        if depth >= max_depth:
            continue

        for key in _LIST_CONTAINER_KEYS:
            value = current.get(key)
            if isinstance(value, dict):
                queue.append((value, depth + 1))

        for key, value in current.items():
            if key in _CHECKLIST_KEYS or key in _LIST_CONTAINER_KEYS:
                continue

            if isinstance(value, list):
                if not value or all(isinstance(item, dict) for item in value):
                    return value

            if isinstance(value, dict):
                queue.append((value, depth + 1))

    return None


def _extract_next_cursor(payload: Any, max_depth: int = 3) -> str | None:
    queue: list[tuple[Any, int]] = [(payload, 0)]
    seen: set[int] = set()

    while queue:
        current, depth = queue.pop(0)
        if isinstance(current, dict):
            current_id = id(current)
            if current_id in seen:
                continue
            seen.add(current_id)

            for key in _PAGINATION_CURSOR_KEYS:
                value = current.get(key)
                if isinstance(value, str) and value.strip():
                    return value

            if depth >= max_depth:
                continue

            for value in current.values():
                if isinstance(value, (dict, list)):
                    queue.append((value, depth + 1))
            continue

        if isinstance(current, list):
            if depth >= max_depth:
                continue
            for item in current:
                if isinstance(item, (dict, list)):
                    queue.append((item, depth + 1))

    return None


def _filter_task_list_by_date(payload: Any, request_input: dict[str, Any] | None) -> Any:
    if not isinstance(payload, list):
        return payload
    if not isinstance(request_input, dict):
        return payload

    requested_date = request_input.get("date")
    if not isinstance(requested_date, str) or not _DATE_ONLY_RE.fullmatch(requested_date):
        return payload

    filtered: list[Any] = []
    for item in payload:
        if _task_matches_requested_date(item, requested_date):
            filtered.append(item)
    return filtered


def _filter_task_list_due_on_or_before(payload: Any, request_input: dict[str, Any] | None) -> Any:
    if not isinstance(payload, list):
        return payload
    if not isinstance(request_input, dict):
        return payload

    requested_date = request_input.get("date")
    if not isinstance(requested_date, str) or not _DATE_ONLY_RE.fullmatch(requested_date):
        return payload

    filtered: list[Any] = []
    for item in payload:
        if _task_matches_due_on_or_before(item, requested_date):
            filtered.append(item)
    return filtered


def _task_matches_requested_date(task: Any, requested_date: str) -> bool:
    due_date = _task_due_iso_date(task)
    return due_date == requested_date


def _task_matches_due_on_or_before(task: Any, requested_date: str) -> bool:
    due_date = _task_due_iso_date(task)
    if due_date is None:
        return False
    return due_date <= requested_date


def _task_due_iso_date(task: Any) -> str | None:
    if not isinstance(task, dict):
        return None

    due = task.get("due")

    candidates: list[Any] = [
        due.get("date") if isinstance(due, dict) else None,
        due.get("datetime") if isinstance(due, dict) else None,
        task.get("due_date"),
        task.get("dueDate"),
        task.get("due_datetime"),
    ]

    for candidate in candidates:
        parsed = _parse_iso_date_candidate(candidate)
        if parsed is not None:
            return parsed

    return None


def _parse_iso_date_candidate(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    if _DATE_ONLY_RE.fullmatch(value):
        return value

    prepared = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(prepared)
    except ValueError:
        candidate = value[:10] if len(value) >= 10 else ""
        return candidate if _DATE_ONLY_RE.fullmatch(candidate) else None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.date().isoformat()


def _normalize_collection(value: Any, mapper: Any) -> Any:
    if isinstance(value, list):
        return [mapper(item) for item in value]
    if isinstance(value, dict):
        return mapper(value)
    return value


def _task_to_toon(task: dict[str, Any]) -> dict[str, Any]:
    checklist_items = _extract_checklist(task)
    done_value = _first_present(task, "isCompleted", "completed", "checked", "complete", "is_completed")
    if done_value is None:
        done_value = _state_to_completed(task.get("state"))

    toon = {
        "i": task.get("id"),
        "n": _clean_text(_first_present(task, "name", "title", "content")),
        "d": _task_description(task),
        "s": _task_start(task),
        "tg": _task_section_ref(task),
        "p": _priority_to_int(task.get("priority")),
        "x": _bool_to_int(done_value),
        "c": [_checklist_item_to_toon(item) for item in checklist_items],
    }
    return _compact_dict(toon)


def _task_section_ref(task: dict[str, Any]) -> str | None:
    direct = _first_present(task, "taskGroupId", "task_group_id", "sectionId", "section_id")
    if direct is not None:
        text = str(direct).strip()
        return text or None

    section = task.get("section")
    if isinstance(section, dict):
        nested = _first_present(section, "id", "sectionId", "section_id")
        if nested is not None:
            text = str(nested).strip()
            return text or None

    return None


def _project_to_toon(project: dict[str, Any]) -> dict[str, Any]:
    toon = {
        "i": project.get("id"),
        "n": _clean_text(_first_present(project, "name", "title")),
        "d": _clean_text(_first_present(project, "description")),
    }
    return _compact_dict(toon)


def _section_to_toon(section: dict[str, Any]) -> dict[str, Any]:
    toon = {
        "i": section.get("id"),
        "n": _clean_text(_first_present(section, "name", "title")),
        "d": _clean_text(_first_present(section, "description")),
        "pr": _first_present(section, "projectId", "project_id"),
    }
    return _compact_dict(toon)


def _checklist_item_to_toon(item: dict[str, Any]) -> dict[str, Any]:
    done_value = _first_present(item, "isCompleted", "completed", "checked", "complete", "is_completed")
    if done_value is None:
        done_value = _state_to_completed(item.get("state"))

    toon = {
        "i": item.get("id"),
        "n": _clean_text(_first_present(item, "name", "title", "content")),
        "x": _bool_to_int(done_value),
    }
    return _compact_dict(toon)


def _task_start(task: dict[str, Any]) -> Any:
    start = _first_present(task, "startDate", "start", "dueDate", "due_datetime")
    if start is not None:
        return start

    due = task.get("due")
    if isinstance(due, dict):
        value = _first_present(due, "datetime", "date")
        if value is not None:
            return value

    return None


def _priority_to_int(value: Any) -> int | None:
    mapping = {"low": 0, "medium": 1, "high": 2}

    if isinstance(value, int) and value in (0, 1, 2):
        return value

    if isinstance(value, int) and value in (1, 2, 3, 4):
        if value <= 2:
            return 0
        if value == 3:
            return 1
        return 2

    if isinstance(value, str):
        text = value.strip().lower()
        if text in mapping:
            return mapping[text]
        if text.isdigit():
            number = int(text)
            return _priority_to_int(number)

    return None


def _bool_to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return 1
        if text in {"0", "false", "no", "n"}:
            return 0
    return 0


def _extract_checklist(task: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _CHECKLIST_KEYS:
        value = task.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


def _task_description(task: dict[str, Any]) -> str | None:
    direct = _clean_text(task.get("description"))
    if direct:
        return direct

    note = _clean_text(task.get("note"))
    if not note:
        return None
    if _NOTE_POINTER_RE.fullmatch(note):
        return None
    return note


def _state_to_completed(state: Any) -> bool | None:
    if state is None:
        return None

    if isinstance(state, int):
        return state == 2

    if isinstance(state, str):
        text = state.strip().lower()
        if text.isdigit():
            return int(text) == 2
        if text in {"done", "completed", "closed"}:
            return True
        if text in {"new", "active", "open", "in_progress"}:
            return False

    return None


def _strip_meta_fields(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _META_FIELDS:
                continue
            normalized = _strip_meta_fields(item)
            if isinstance(normalized, dict) and not normalized:
                continue
            if isinstance(normalized, list) and not normalized:
                continue
            cleaned[key] = normalized
        return cleaned

    if isinstance(value, list):
        cleaned_list: list[Any] = []
        for item in value:
            normalized = _strip_meta_fields(item)
            if isinstance(normalized, dict) and not normalized:
                continue
            if isinstance(normalized, list) and not normalized:
                continue
            cleaned_list.append(normalized)
        return cleaned_list

    return value


def _apply_local_pagination(
    method_name: str,
    data: Any,
    request_input: dict[str, Any] | None,
) -> Any:
    if ".list" not in method_name:
        return data
    if not isinstance(data, list):
        return data
    if not isinstance(request_input, dict):
        return data

    size = request_input.get("size")
    page = request_input.get("page", 1)
    if not isinstance(size, int) or size <= 0:
        return data
    if not isinstance(page, int) or page <= 0:
        page = 1

    start = (page - 1) * size
    if start >= len(data):
        return []

    end = start + size
    return data[start:end]


def _compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        compact[key] = value
    return compact
