from __future__ import annotations

from datetime import date, timedelta
import io
import json
import pathlib
import sys
import unittest
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.app import create_app
from todoist_proxy.cache import ResponseCache
from todoist_proxy.client import MissingTokenError


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, spec):
        self.calls.append(spec)
        return self.payload


class TokenAwareFactory:
    def __init__(self, payload):
        self.payload = payload
        self.clients = {}

    def __call__(self, token=None):
        key = token or "env-token"
        if key not in self.clients:
            self.clients[key] = DummyClient(self.payload)
        return self.clients[key]


class MutationAwareClient:
    def __init__(self):
        self.calls = []

    def request(self, spec):
        self.calls.append(spec)
        if spec.method == "GET":
            return {"id": "t1", "content": "Task"}
        return {"ok": True}


def call_wsgi(app, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None):
    environ = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    path_info, _, query_string = path.partition("?")
    environ["PATH_INFO"] = path_info
    environ["QUERY_STRING"] = query_string
    environ["wsgi.input"] = io.BytesIO(body)
    environ["CONTENT_LENGTH"] = str(len(body))
    for key, value in (headers or {}).items():
        environ[f"HTTP_{key.upper().replace('-', '_')}"] = value

    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    chunks = app(environ, start_response)
    response_body = b"".join(chunks)
    status_code = int(captured["status"].split(" ", 1)[0])
    payload = json.loads(response_body.decode("utf-8"))
    return status_code, payload


class AppTests(unittest.TestCase):
    def test_methods_endpoint(self) -> None:
        app = create_app(client_factory=lambda token=None: DummyClient({}))

        status, payload = call_wsgi(app, "GET", "/methods")

        self.assertEqual(200, status)
        self.assertIn("methods", payload)
        names = [item["name"] for item in payload["methods"]]
        self.assertIn("task.list", names)

    def test_invalid_json_input_returns_validation_error(self) -> None:
        app = create_app(client_factory=lambda token=None: DummyClient({}))

        status, payload = call_wsgi(app, "POST", "/raw/task.list", body=b"{")

        self.assertEqual(400, status)
        self.assertEqual(3, payload["error"]["status"])

    def test_today_tasks_get_endpoint_returns_toon_list(self) -> None:
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        client = DummyClient(
            {
                "results": [
                    {"id": "t1", "content": "Task 1", "due": {"date": today}},
                    {"id": "t2", "content": "No due"},
                    {"id": "t3", "content": "Future", "due": {"date": tomorrow}},
                ],
                "next_cursor": "cursor-2",
            }
        )
        app = create_app(client_factory=lambda token=None: client)

        status, payload = call_wsgi(
            app,
            "GET",
            "/tasks/today?cursor=cursor-1&limit=25",
            headers={"Authorization": "Bearer token-a"},
        )

        self.assertEqual(200, status)
        self.assertEqual({"d": [{"n": "Task 1", "s": today}], "next_cursor": "cursor-2"}, payload)
        self.assertEqual(1, len(client.calls))
        call = client.calls[0]
        self.assertEqual("GET", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual({"filter": "overdue | today", "cursor": "cursor-1", "limit": 25}, call.query)

    def test_today_tasks_get_invalid_limit_returns_validation_error(self) -> None:
        app = create_app(client_factory=lambda token=None: DummyClient({}))

        status, payload = call_wsgi(
            app,
            "GET",
            "/tasks/today?limit=abc",
            headers={"Authorization": "Bearer token-a"},
        )

        self.assertEqual(400, status)
        self.assertEqual(3, payload["error"]["status"])
        self.assertIn("limit", payload["error"]["message"])

    def test_raw_mode_passthrough(self) -> None:
        app = create_app(client_factory=lambda token=None: DummyClient({"data": {"id": "t1", "content": "Task"}}))

        status, payload = call_wsgi(
            app,
            "POST",
            "/raw/task.get",
            body=b'{"task_id":"t1"}',
        )

        self.assertEqual(200, status)
        self.assertEqual({"data": {"id": "t1", "content": "Task"}}, payload)

    def test_toon_mode_transforms_output(self) -> None:
        app = create_app(
            client_factory=lambda token=None: DummyClient(
                {
                    "id": "t1",
                    "content": "Task",
                    "description": "Desc",
                    "due": {"datetime": "2026-02-18T00:00:00Z"},
                    "is_completed": False,
                }
            )
        )

        status, payload = call_wsgi(
            app,
            "POST",
            "/toon/task.get",
            body=b'{"task_id":"t1"}',
        )

        self.assertEqual(200, status)
        self.assertEqual("Task", payload["d"]["n"])
        self.assertEqual("2026-02-18T03:00:00+03:00", payload["d"]["s"])

    def test_toon_task_close_returns_ok(self) -> None:
        app = create_app(client_factory=lambda token=None: DummyClient({}))

        status, payload = call_wsgi(
            app,
            "POST",
            "/toon/task.close",
            body=b'{"task_id":"t1"}',
        )

        self.assertEqual(200, status)
        self.assertEqual({"d": {"ok": 1}}, payload)

    def test_missing_token_returns_status_2(self) -> None:
        def factory(token=None):
            raise MissingTokenError("request token is not set")

        app = create_app(client_factory=factory)

        status, payload = call_wsgi(app, "POST", "/raw/task.list", body=b"{}")

        self.assertEqual(401, status)
        self.assertEqual(2, payload["error"]["status"])

    def test_default_route_uses_toon_mode(self) -> None:
        app = create_app(
            client_factory=lambda token=None: DummyClient(
                {
                    "tasks": [
                        {"id": "t1", "content": "Task 1", "is_completed": False},
                    ]
                }
            )
        )

        status, payload = call_wsgi(app, "POST", "/task.list", body=b'{"page":1,"size":1}')

        self.assertEqual(200, status)
        self.assertEqual({"d": [{"n": "Task 1"}]}, payload)

    def test_unknown_method_fails_with_validation_status(self) -> None:
        app = create_app(client_factory=lambda token=None: DummyClient({}))

        status, payload = call_wsgi(app, "POST", "/raw/kanban.list", body=b"{}")

        self.assertEqual(400, status)
        self.assertEqual(3, payload["error"]["status"])

    def test_get_cache_isolated_by_token(self) -> None:
        factory = TokenAwareFactory({"id": "t1", "content": "Task"})
        app = create_app(client_factory=factory, response_cache=ResponseCache(ttl_seconds=60, max_size=100))

        call_wsgi(
            app,
            "POST",
            "/raw/task.get",
            body=b'{"task_id":"t1"}',
            headers={"Authorization": "Bearer token-a"},
        )
        call_wsgi(
            app,
            "POST",
            "/raw/task.get",
            body=b'{"task_id":"t1"}',
            headers={"Authorization": "Bearer token-a"},
        )
        call_wsgi(
            app,
            "POST",
            "/raw/task.get",
            body=b'{"task_id":"t1"}',
            headers={"Authorization": "Bearer token-b"},
        )

        self.assertEqual(1, len(factory.clients["token-a"].calls))
        self.assertEqual(1, len(factory.clients["token-b"].calls))

    def test_write_request_invalidates_token_cache(self) -> None:
        client = MutationAwareClient()
        app = create_app(
            client_factory=lambda token=None: client,
            response_cache=ResponseCache(ttl_seconds=60, max_size=100),
        )
        headers = {"Authorization": "Bearer token-a"}

        call_wsgi(app, "POST", "/raw/task.get", body=b'{"task_id":"t1"}', headers=headers)
        call_wsgi(app, "POST", "/raw/task.get", body=b'{"task_id":"t1"}', headers=headers)
        call_wsgi(app, "POST", "/raw/task.update", body=b'{"task_id":"t1","name":"Updated"}', headers=headers)
        call_wsgi(app, "POST", "/raw/task.get", body=b'{"task_id":"t1"}', headers=headers)

        self.assertEqual(3, len(client.calls))


if __name__ == "__main__":
    unittest.main()
