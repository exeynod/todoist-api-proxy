import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.client import ApiError, MissingTokenError, TodoistClient, _FileTokenBucketRateLimiter
from todoist_proxy.schemas import RequestSpec


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text="") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class DummySession:
    def __init__(self, response: DummyResponse) -> None:
        self.response = response
        self.last_call = None

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.last_call = {
            "method": method,
            "url": url,
            "params": params,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return self.response


class ClientTests(unittest.TestCase):
    def test_missing_token_raises(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingTokenError):
                TodoistClient()

    def test_bearer_header_and_url(self) -> None:
        response = DummyResponse(status_code=200, payload={"ok": True})
        session = DummySession(response)
        limiter = mock.Mock()
        client = TodoistClient(token="abc123", session=session, rate_limiter=limiter)

        payload = client.request(RequestSpec(method="GET", path="/tasks", query={"project_id": "p1"}, body={}))

        self.assertEqual({"ok": True}, payload)
        self.assertEqual("GET", session.last_call["method"])
        self.assertEqual("https://api.todoist.com/api/v1/tasks", session.last_call["url"])
        self.assertEqual("Bearer abc123", session.last_call["headers"]["Authorization"])
        self.assertEqual(5, session.last_call["timeout"])
        limiter.acquire.assert_called_once_with(client.token_scope)

    def test_api_error_raises(self) -> None:
        response = DummyResponse(status_code=401, payload={"message": "Unauthorized"})
        session = DummySession(response)
        limiter = mock.Mock()
        client = TodoistClient(token="abc123", session=session, rate_limiter=limiter)

        with self.assertRaises(ApiError) as ctx:
            client.request(RequestSpec(method="GET", path="/tasks", query={}, body={}))

        self.assertEqual(401, ctx.exception.status)
        self.assertEqual("Unauthorized", ctx.exception.message)

    def test_timeout_can_be_overridden_via_env(self) -> None:
        response = DummyResponse(status_code=200, payload={"ok": True})
        session = DummySession(response)
        limiter = mock.Mock()
        with mock.patch.dict(
            os.environ,
            {
                "TODOIST_TIMEOUT_SECONDS": "2.5",
            },
            clear=False,
        ):
            client = TodoistClient(token="abc123", session=session, rate_limiter=limiter)
            client.request(RequestSpec(method="GET", path="/tasks", query={}, body={}))

        self.assertEqual(2.5, session.last_call["timeout"])

    def test_clients_with_different_tokens_use_different_rate_limit_scopes(self) -> None:
        session_a = DummySession(DummyResponse(status_code=200, payload={"ok": True}))
        session_b = DummySession(DummyResponse(status_code=200, payload={"ok": True}))
        limiter = mock.Mock()

        client_a = TodoistClient(token="token-a", session=session_a, rate_limiter=limiter)
        client_b = TodoistClient(token="token-b", session=session_b, rate_limiter=limiter)

        client_a.request(RequestSpec(method="GET", path="/tasks", query={}, body={}))
        client_b.request(RequestSpec(method="GET", path="/tasks", query={}, body={}))

        self.assertNotEqual(client_a.token_scope, client_b.token_scope)
        self.assertEqual(
            [mock.call(client_a.token_scope), mock.call(client_b.token_scope)],
            limiter.acquire.call_args_list,
        )

    def test_file_rate_limiter_isolated_by_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            limiter = _FileTokenBucketRateLimiter(
                state_path=f"{tmp_dir}/rate.json",
                rate_per_second=0.1,
                burst_capacity=1.0,
            )

            wait_a1 = limiter._try_acquire_once("scope-a")
            wait_b1 = limiter._try_acquire_once("scope-b")
            wait_a2 = limiter._try_acquire_once("scope-a")

        self.assertEqual(0.0, wait_a1)
        self.assertEqual(0.0, wait_b1)
        self.assertGreater(wait_a2, 0.0)


if __name__ == "__main__":
    unittest.main()
