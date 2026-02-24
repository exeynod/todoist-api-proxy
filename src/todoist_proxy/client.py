from __future__ import annotations

import json as jsonlib
from datetime import datetime
import os
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised in runtime envs without requests
    requests = None

from todoist_proxy.models import TokenBucketState, token_fingerprint
from todoist_proxy.schemas import JsonDict, RequestSpec

BASE_URL = "https://api.todoist.com/api/v1"
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_RATE_LIMIT_RPS = 0.2
DEFAULT_RATE_LIMIT_BURST = 2.0
DEFAULT_RATE_LIMIT_STATE_FILE = "/tmp/todoist_proxy_rate_limit.json"
DEFAULT_API_LOG_DIR = "/tmp"

_API_LOG_LOCK = threading.Lock()


class MissingTokenError(RuntimeError):
    """Raised when request token is missing."""


@dataclass
class ApiError(Exception):
    status: int
    message: str
    payload: Any | None = None

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.message}"


class TodoistClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = BASE_URL,
        session: Any | None = None,
        rate_limiter: Any | None = None,
    ) -> None:
        resolved_token = token
        if not resolved_token:
            raise MissingTokenError("request token is not set")

        self.token = resolved_token
        self.token_scope = token_fingerprint(resolved_token)
        self.base_url = base_url.rstrip("/")
        self.session = session or _default_session()
        self.timeout_seconds = _read_positive_float(
            env_name="TODOIST_TIMEOUT_SECONDS",
            default=DEFAULT_TIMEOUT_SECONDS,
        )
        self.rate_limiter = rate_limiter or _build_rate_limiter()

    def request(self, spec: RequestSpec) -> Any:
        self.rate_limiter.acquire(self.token_scope)

        headers: JsonDict = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

        if spec.body:
            headers["Content-Type"] = "application/json"

        url = f"{self.base_url}{spec.path}"
        started_at = time.monotonic()
        status_code: int | None = None
        error_message: str | None = None
        try:
            response = self.session.request(
                spec.method,
                url,
                params=spec.query or None,
                json=spec.body or None,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            status_code = int(getattr(response, "status_code", 0))
            payload = _decode_payload(response)
            if status_code >= 400:
                error_message = _extract_error_message(response, payload)
                raise ApiError(
                    status=status_code,
                    message=error_message,
                    payload=payload,
                )
            return payload
        except ApiError as exc:
            if status_code is None:
                status_code = exc.status
            if error_message is None:
                error_message = exc.message
            raise
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            raise
        finally:
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            log_context = getattr(self, "_proxy_log_context", None)
            proxy_mode = None
            proxy_path = None
            proxy_method = None
            if isinstance(log_context, dict):
                mode_value = log_context.get("mode")
                path_value = log_context.get("path")
                method_value = log_context.get("method_name")
                proxy_mode = str(mode_value) if mode_value else None
                proxy_path = str(path_value) if path_value else None
                proxy_method = str(method_value) if method_value else None
            _append_api_call_log(
                method=spec.method,
                url=url,
                status=status_code,
                token_scope=self.token_scope,
                elapsed_ms=elapsed_ms,
                error=error_message,
                proxy_mode=proxy_mode,
                proxy_path=proxy_path,
                proxy_method=proxy_method,
            )


def _decode_payload(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        text = getattr(response, "text", "")
        return {"message": text} if text else {}


def _extract_error_message(response: Any, payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail", "title"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested_message = value.get("message")
                if isinstance(nested_message, str) and nested_message.strip():
                    return nested_message
        return f"HTTP error {response.status_code}"

    if isinstance(payload, str) and payload.strip():
        return payload.strip()

    return f"HTTP error {response.status_code}"


def _default_session() -> Any:
    if requests is not None:
        return requests.Session()
    return _StdlibSession()


def _append_api_call_log(
    *,
    method: str,
    url: str,
    status: int | None,
    token_scope: str,
    elapsed_ms: float,
    error: str | None,
    proxy_mode: str | None = None,
    proxy_path: str | None = None,
    proxy_method: str | None = None,
) -> None:
    now = datetime.now()
    payload: dict[str, Any] = {
        "ts": now.isoformat(timespec="seconds"),
        "method": method.upper(),
        "url": url,
        "status": status,
        "token_scope": token_scope,
        "elapsed_ms": round(elapsed_ms, 3),
    }
    if proxy_mode:
        payload["proxy_mode"] = proxy_mode
    if proxy_path:
        payload["proxy_path"] = proxy_path
    if proxy_method:
        payload["proxy_method"] = proxy_method
    if error:
        payload["error"] = error

    log_path = _api_log_file_path(now)
    line = jsonlib.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    try:
        with _API_LOG_LOCK:
            with open(log_path, "a", encoding="utf-8") as fp:
                fp.write(line)
                fp.write("\n")
    except OSError:
        # Logging is best-effort and must not break request handling.
        return


def _api_log_file_path(now: datetime) -> str:
    return os.path.join(DEFAULT_API_LOG_DIR, f"logs_{now.strftime('%Y%m%d')}.log")


class _FileTokenBucketRateLimiter:
    def __init__(
        self,
        state_path: str,
        rate_per_second: float,
        burst_capacity: float,
    ) -> None:
        self.state_path = state_path
        self.rate_per_second = rate_per_second
        self.burst_capacity = burst_capacity

    def acquire(self, scope_key: str) -> None:
        while True:
            wait_for = self._try_acquire_once(scope_key)
            if wait_for <= 0:
                return
            time.sleep(wait_for)

    def _try_acquire_once(self, scope_key: str) -> float:
        import fcntl

        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        now = time.monotonic()
        with open(self.state_path, "a+", encoding="utf-8") as state_file:
            fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
            state_file.seek(0)
            raw_state = state_file.read().strip()
            state_by_scope = _parse_rate_state(raw_state, now, self.burst_capacity)
            scope = scope_key or "anon"
            state = state_by_scope.get(
                scope,
                TokenBucketState(tokens=self.burst_capacity, last_refill=now),
            )

            elapsed = max(0.0, now - state.last_refill)
            tokens = min(
                self.burst_capacity,
                state.tokens + elapsed * self.rate_per_second,
            )

            if tokens >= 1.0:
                state_by_scope[scope] = TokenBucketState(tokens=tokens - 1.0, last_refill=now)
                _write_rate_state(state_file, _prune_rate_state(state_by_scope, now))
                return 0.0

            missing_tokens = 1.0 - tokens
            wait_for = missing_tokens / self.rate_per_second
            state_by_scope[scope] = TokenBucketState(tokens=tokens, last_refill=now)
            _write_rate_state(state_file, _prune_rate_state(state_by_scope, now))
            return wait_for


def _parse_rate_state(
    raw_state: str,
    now: float,
    default_tokens: float,
) -> dict[str, TokenBucketState]:
    if not raw_state:
        return {}

    try:
        parsed = jsonlib.loads(raw_state)
    except ValueError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    # Backward compatibility with previous single-bucket shape.
    if "tokens" in parsed and "last_refill" in parsed:
        tokens = parsed.get("tokens")
        last_refill = parsed.get("last_refill")
        if isinstance(tokens, (int, float)) and isinstance(last_refill, (int, float)):
            return {
                "legacy": TokenBucketState(tokens=float(tokens), last_refill=float(last_refill)),
            }
        return {}

    state: dict[str, TokenBucketState] = {}
    for scope, value in parsed.items():
        if not isinstance(scope, str) or not isinstance(value, dict):
            continue
        tokens = value.get("tokens")
        last_refill = value.get("last_refill")
        if not isinstance(tokens, (int, float)) or not isinstance(last_refill, (int, float)):
            continue
        state[scope] = TokenBucketState(tokens=float(tokens), last_refill=float(last_refill))

    if not state:
        # Initialize current format lazily to avoid write on every malformed input.
        return {"anon": TokenBucketState(tokens=default_tokens, last_refill=now)}

    return state


def _prune_rate_state(
    state_by_scope: dict[str, TokenBucketState],
    now: float,
    max_idle_seconds: float = 24 * 60 * 60,
) -> dict[str, TokenBucketState]:
    return {
        scope: state
        for scope, state in state_by_scope.items()
        if now - state.last_refill <= max_idle_seconds
    }


def _write_rate_state(state_file: Any, state: dict[str, TokenBucketState]) -> None:
    serialized = {scope: value.to_dict() for scope, value in state.items()}
    state_file.seek(0)
    state_file.truncate()
    state_file.write(jsonlib.dumps(serialized))
    state_file.flush()


def _read_positive_float(env_name: str, default: float) -> float:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def _build_rate_limiter() -> _FileTokenBucketRateLimiter:
    rate_per_second = _read_positive_float(
        env_name="TODOIST_RATE_LIMIT_RPS",
        default=DEFAULT_RATE_LIMIT_RPS,
    )
    burst_capacity = _read_positive_float(
        env_name="TODOIST_RATE_LIMIT_BURST",
        default=DEFAULT_RATE_LIMIT_BURST,
    )
    state_path = os.getenv("TODOIST_RATE_LIMIT_STATE_FILE", DEFAULT_RATE_LIMIT_STATE_FILE)
    return _FileTokenBucketRateLimiter(
        state_path=state_path,
        rate_per_second=rate_per_second,
        burst_capacity=burst_capacity,
    )


class _StdlibResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        if not self.text:
            return {}
        return jsonlib.loads(self.text)


class _StdlibSession:
    def request(
        self,
        method: str,
        url: str,
        params: JsonDict | None = None,
        json: JsonDict | None = None,
        headers: JsonDict | None = None,
        timeout: int | float | None = None,
    ) -> _StdlibResponse:
        full_url = _join_query(url, params or {})
        data = None
        if json is not None:
            data = jsonlib.dumps(json).encode("utf-8")

        req = urllib_request.Request(
            url=full_url,
            data=data,
            method=method,
            headers=headers or {},
        )

        try:
            with urllib_request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return _StdlibResponse(status_code=response.status, text=body)
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return _StdlibResponse(status_code=exc.code, text=body)
        except urllib_error.URLError as exc:
            raise ApiError(status=0, message=f"network error: {exc.reason}") from exc


def _join_query(url: str, params: JsonDict) -> str:
    if not params:
        return url
    query = urllib_parse.urlencode(params)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{query}"
