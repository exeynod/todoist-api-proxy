from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ErrorDetail:
    status: int
    message: str


@dataclass(frozen=True)
class ErrorPayload:
    error: ErrorDetail

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {"error": {"status": self.error.status, "message": self.error.message}}


@dataclass(frozen=True)
class CacheKey:
    token_scope: str
    mode: str
    method_name: str
    input_payload: str

    @classmethod
    def from_request(
        cls,
        *,
        token: str,
        mode: str,
        method_name: str,
        payload: dict[str, Any] | None,
    ) -> "CacheKey":
        normalized_payload = json.dumps(
            payload or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            token_scope=token_fingerprint(token),
            mode=mode,
            method_name=method_name,
            input_payload=normalized_payload,
        )


@dataclass
class CacheEntry:
    payload: Any
    expires_at: float


@dataclass(frozen=True)
class TokenBucketState:
    tokens: float
    last_refill: float

    def to_dict(self) -> dict[str, float]:
        return {"tokens": self.tokens, "last_refill": self.last_refill}


def token_fingerprint(token: str | None) -> str:
    if not token:
        return "anon"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest[:24]
