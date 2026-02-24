from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from http import HTTPStatus
import json
from typing import Any, Callable
from urllib.parse import parse_qs, unquote

from todoist_proxy.cache import ResponseCache, build_response_cache
from todoist_proxy.client import ApiError, MissingTokenError, TodoistClient
from todoist_proxy.methods import get_schema, method_catalog_rows
from todoist_proxy.models import CacheKey, ErrorDetail, ErrorPayload, token_fingerprint
from todoist_proxy.schemas import InputValidationError, RequestSpec
from todoist_proxy.service import execute_method
from todoist_proxy.toon import to_toon_response

EXIT_API_ERROR = 1
EXIT_NO_TOKEN = 2
EXIT_VALIDATION = 3


@dataclass
class ProxyHttpError(Exception):
    http_status: int
    error_status: int
    message: str


def create_app(
    client_factory: Callable[..., Any] | None = None,
    response_cache: ResponseCache | None = None,
):
    factory = client_factory or _default_client_factory
    cache = response_cache or build_response_cache()

    def app(environ: dict[str, Any], start_response: Callable[..., Any]):
        try:
            status_code, payload = _handle_request(environ, factory, cache)
        except ProxyHttpError as exc:
            status_code = exc.http_status
            payload = ErrorPayload(error=ErrorDetail(status=exc.error_status, message=exc.message)).to_dict()

        body = _json_bytes(payload)
        status_line = f"{status_code} {_status_text(status_code)}"
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ]
        start_response(status_line, headers)
        return [body]

    return app


def _handle_request(
    environ: dict[str, Any],
    client_factory: Callable[..., Any],
    response_cache: ResponseCache,
) -> tuple[int, Any]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "") or ""

    if method == "GET" and path == "/methods":
        return 200, {"methods": method_catalog_rows()}
    if method == "GET" and path == "/tasks/today":
        return _handle_today_tasks_request(environ, client_factory, response_cache)

    if method != "POST":
        raise ProxyHttpError(http_status=404, error_status=404, message="not found")

    mode, method_name = _resolve_mode_and_method(path)
    if mode is None or not method_name:
        raise ProxyHttpError(http_status=404, error_status=404, message="not found")

    payload = _parse_json_object(environ)
    request_token = _extract_request_token(environ)

    try:
        client = _build_client(client_factory, request_token)
    except MissingTokenError as exc:
        raise ProxyHttpError(http_status=401, error_status=EXIT_NO_TOKEN, message=str(exc)) from exc

    token_scope = token_fingerprint(getattr(client, "token", request_token))

    try:
        schema = get_schema(method_name)
        cache_key: CacheKey | None = None
        if schema.http_method == "GET":
            cache_key = CacheKey.from_request(
                token=getattr(client, "token", request_token),
                mode=mode,
                method_name=method_name,
                payload=payload,
            )
            cached_response = response_cache.get(cache_key)
            if cached_response is not None:
                return 200, cached_response

        _set_client_log_context(
            client,
            mode=mode,
            request_path=path,
            method_name=method_name,
        )
        try:
            raw_result = execute_method(client, method_name, payload)
        finally:
            _clear_client_log_context(client)
    except InputValidationError as exc:
        raise ProxyHttpError(http_status=400, error_status=EXIT_VALIDATION, message=str(exc)) from exc
    except ApiError as exc:
        http_status = exc.status if 400 <= exc.status <= 599 else 502
        error_status = exc.status if exc.status > 0 else EXIT_API_ERROR
        raise ProxyHttpError(http_status=http_status, error_status=error_status, message=exc.message) from exc

    if mode == "raw":
        response_payload = raw_result
    else:
        response_payload = to_toon_response(method_name, raw_result, request_input=payload)

    if schema.http_method == "GET":
        response_cache.set(cache_key, response_payload)
    else:
        response_cache.invalidate_token_scope(token_scope)

    return 200, response_payload


def _handle_today_tasks_request(
    environ: dict[str, Any],
    client_factory: Callable[..., Any],
    response_cache: ResponseCache,
) -> tuple[int, Any]:
    request_token = _extract_request_token(environ)
    query_options = _parse_today_tasks_query(environ)

    try:
        client = _build_client(client_factory, request_token)
    except MissingTokenError as exc:
        raise ProxyHttpError(http_status=401, error_status=EXIT_NO_TOKEN, message=str(exc)) from exc

    cache_payload = {"filter": "overdue | today"}
    cache_payload.update(query_options)
    cache_key = CacheKey.from_request(
        token=getattr(client, "token", request_token),
        mode="toon",
        method_name="task.today",
        payload=cache_payload,
    )
    cached_response = response_cache.get(cache_key)
    if cached_response is not None:
        return 200, cached_response

    spec = RequestSpec(
        method="GET",
        path="/tasks",
        query=cache_payload,
        body={},
    )
    try:
        _set_client_log_context(
            client,
            mode="toon",
            request_path="/tasks/today",
            method_name="task.list_today",
        )
        try:
            raw_result = client.request(spec)
        finally:
            _clear_client_log_context(client)
    except ApiError as exc:
        http_status = exc.status if 400 <= exc.status <= 599 else 502
        error_status = exc.status if exc.status > 0 else EXIT_API_ERROR
        raise ProxyHttpError(http_status=http_status, error_status=error_status, message=exc.message) from exc

    response_payload = to_toon_response(
        "task.list_today",
        raw_result,
        request_input={"date": date.today().isoformat()},
    )
    response_cache.set(cache_key, response_payload)
    return 200, response_payload


def _parse_today_tasks_query(environ: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=False)
    query: dict[str, Any] = {}

    cursor_values = parsed.get("cursor") or []
    if cursor_values:
        cursor = cursor_values[0].strip()
        if cursor:
            query["cursor"] = cursor

    limit_values = parsed.get("limit") or []
    if not limit_values:
        return query

    raw_limit = limit_values[0].strip()
    if not raw_limit:
        return query

    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise ProxyHttpError(
            http_status=400,
            error_status=EXIT_VALIDATION,
            message="invalid query input: limit must be an integer > 0",
        ) from exc

    if limit <= 0:
        raise ProxyHttpError(
            http_status=400,
            error_status=EXIT_VALIDATION,
            message="invalid query input: limit must be an integer > 0",
        )

    query["limit"] = limit
    return query


def _resolve_mode_and_method(path: str) -> tuple[str | None, str | None]:
    if path.startswith("/raw/"):
        return "raw", unquote(path[len("/raw/") :])

    if path.startswith("/toon/"):
        return "toon", unquote(path[len("/toon/") :])

    # Default mode parity with CLI: omitted command means toon mode.
    if path.startswith("/") and path.count("/") == 1 and len(path) > 1:
        return "toon", unquote(path[1:])

    return None, None


def _parse_json_object(environ: dict[str, Any]) -> dict[str, Any]:
    raw = _read_body(environ)
    if not raw:
        return {}

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ProxyHttpError(
            http_status=400,
            error_status=EXIT_VALIDATION,
            message="invalid JSON input: body must be UTF-8",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProxyHttpError(
            http_status=400,
            error_status=EXIT_VALIDATION,
            message=f"invalid JSON input: {exc.msg}",
        ) from exc

    if not isinstance(parsed, dict):
        raise ProxyHttpError(
            http_status=400,
            error_status=EXIT_VALIDATION,
            message="input must be a JSON object",
        )

    return parsed


def _extract_request_token(environ: dict[str, Any]) -> str | None:
    auth_header = str(environ.get("HTTP_AUTHORIZATION", "")).strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    alt_header = str(environ.get("HTTP_X_TODOIST_ACCESS_TOKEN", "")).strip()
    if alt_header:
        return alt_header

    return None


def _read_body(environ: dict[str, Any]) -> bytes:
    stream = environ.get("wsgi.input")
    if stream is None:
        return b""

    content_length = environ.get("CONTENT_LENGTH", "").strip()
    if not content_length:
        return stream.read() or b""

    try:
        length = int(content_length)
    except ValueError:
        raise ProxyHttpError(
            http_status=400,
            error_status=EXIT_VALIDATION,
            message="invalid JSON input: invalid content length",
        )

    return stream.read(length) or b""


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _status_text(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Unknown Status"


def _default_client_factory(token: str | None) -> TodoistClient:
    return TodoistClient(token=token)


def _build_client(client_factory: Callable[..., Any], token: str | None) -> Any:
    try:
        return client_factory(token)
    except TypeError:
        return client_factory()


def _set_client_log_context(
    client: Any,
    *,
    mode: str,
    request_path: str,
    method_name: str,
) -> None:
    setattr(
        client,
        "_proxy_log_context",
        {
            "mode": mode,
            "path": request_path,
            "method_name": method_name,
        },
    )


def _clear_client_log_context(client: Any) -> None:
    if hasattr(client, "_proxy_log_context"):
        delattr(client, "_proxy_log_context")


app = create_app()
