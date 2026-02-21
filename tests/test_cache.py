import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.cache import ResponseCache
from todoist_proxy.models import CacheKey


class CacheTests(unittest.TestCase):
    def test_cache_key_uses_token_scope(self) -> None:
        key_a = CacheKey.from_request(
            token="token-a",
            mode="raw",
            method_name="task.get",
            payload={"task_id": "t1"},
        )
        key_b = CacheKey.from_request(
            token="token-b",
            mode="raw",
            method_name="task.get",
            payload={"task_id": "t1"},
        )
        self.assertNotEqual(key_a.token_scope, key_b.token_scope)
        self.assertNotEqual(key_a, key_b)

    def test_invalidate_token_scope_only_removes_matching_entries(self) -> None:
        cache = ResponseCache(ttl_seconds=120, max_size=100, now_fn=lambda: 10.0)
        key_a = CacheKey.from_request(
            token="token-a",
            mode="raw",
            method_name="task.get",
            payload={"task_id": "t1"},
        )
        key_b = CacheKey.from_request(
            token="token-b",
            mode="raw",
            method_name="task.get",
            payload={"task_id": "t1"},
        )

        cache.set(key_a, {"id": "a"})
        cache.set(key_b, {"id": "b"})
        cache.invalidate_token_scope(key_a.token_scope)

        self.assertIsNone(cache.get(key_a))
        self.assertEqual({"id": "b"}, cache.get(key_b))


if __name__ == "__main__":
    unittest.main()
