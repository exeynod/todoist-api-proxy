from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

JsonDict = dict[str, Any]
ParamMap = tuple[tuple[str, str], ...]


class InputValidationError(ValueError):
    """Raised when request input does not match method schema."""


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    query: JsonDict
    body: JsonDict


@dataclass(frozen=True)
class MethodSchema:
    name: str
    http_method: str
    path_template: str
    description: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    path_params: tuple[str, ...]
    query_params: ParamMap
    body_params: ParamMap
    toon_output: str

    @property
    def allowed(self) -> tuple[str, ...]:
        return self.required + self.optional

    def validate_input(self, payload: Any) -> JsonDict:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise InputValidationError("input must be a JSON object")

        missing = [k for k in self.required if k not in payload or payload[k] is None]
        if missing:
            raise InputValidationError(
                f"missing required fields for '{self.name}': {', '.join(sorted(missing))}"
            )

        unknown = sorted(set(payload.keys()) - set(self.allowed))
        if unknown:
            raise InputValidationError(
                f"unknown fields for '{self.name}': {', '.join(unknown)}"
            )

        return dict(payload)

    def to_request(self, payload: Any) -> RequestSpec:
        data = self.validate_input(payload)

        path = self.path_template
        for param in self.path_params:
            path = path.replace(f"{{{param}}}", quote(str(data[param]), safe=""))

        query: JsonDict = {}
        for input_key, output_key in self.query_params:
            if input_key in data and data[input_key] is not None:
                query[output_key] = data[input_key]

        body: JsonDict = {}
        for input_key, output_key in self.body_params:
            if input_key in data and data[input_key] is not None:
                body[output_key] = data[input_key]

        return RequestSpec(method=self.http_method, path=path, query=query, body=body)
