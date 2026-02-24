import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.toon import convert_datetimes_to_msk, to_toon_response


class ToonTests(unittest.TestCase):
    def test_task_minimal_projection(self) -> None:
        raw = {
            "id": "t1",
            "content": "Task",
            "description": "Desc",
            "due": {"datetime": "2026-02-18T00:00:00Z"},
            "priority": 4,
            "is_completed": False,
            "updatedAt": "2026-02-18T08:00:00Z",
            "subtasks": [{"id": "c1", "content": "item1", "is_completed": True}],
        }

        out = to_toon_response("task.get", raw)

        self.assertEqual(
            {
                "d": {
                    "n": "Task",
                    "d": "Desc",
                    "s": "2026-02-18T03:00:00+03:00",
                    "c": [{"n": "item1"}],
                }
            },
            out,
        )

    def test_datetime_conversion_keeps_date_only(self) -> None:
        payload = {
            "due": {"date": "2026-02-18", "datetime": "2026-02-18T00:00:00Z"},
            "createdAt": "2026-02-18T00:00:00Z",
            "nested": {"completedDate": "2026-02-18T21:00:00+00:00"},
        }

        converted = convert_datetimes_to_msk(payload)

        self.assertEqual("2026-02-18", converted["due"]["date"])
        self.assertEqual("2026-02-18T03:00:00+03:00", converted["due"]["datetime"])
        self.assertEqual("2026-02-18T03:00:00+03:00", converted["createdAt"])
        self.assertEqual("2026-02-19T00:00:00+03:00", converted["nested"]["completedDate"])

    def test_delete_always_ok(self) -> None:
        out = to_toon_response("task.delete", {"any": "payload"})
        self.assertEqual({"d": {"ok": 1}}, out)

    def test_close_always_ok(self) -> None:
        out = to_toon_response("task.close", {"any": "payload"})
        self.assertEqual({"d": {"ok": 1}}, out)

    def test_task_list_extracts_nested_items(self) -> None:
        raw = {
            "data": {
                "items": [
                    {
                        "id": "t1",
                        "content": "Task",
                        "description": "Desc",
                        "due": {"datetime": "2026-02-18T00:00:00Z"},
                        "priority": 4,
                        "is_completed": False,
                    }
                ],
                "total": 1,
            }
        }

        out = to_toon_response("task.list", raw)

        self.assertEqual(
            {
                "d": [
                    {
                        "n": "Task",
                        "d": "Desc",
                        "s": "2026-02-18T03:00:00+03:00",
                    }
                ]
            },
            out,
        )

    def test_task_list_without_array_returns_empty_list(self) -> None:
        out = to_toon_response("task.list", {"status": "ok"})
        self.assertEqual({"d": []}, out)

    def test_task_list_applies_local_pagination_when_server_ignores_size(self) -> None:
        raw = {
            "tasks": [
                {"id": "t1", "content": "Task 1", "is_completed": False},
                {"id": "t2", "content": "Task 2", "is_completed": False},
                {"id": "t3", "content": "Task 3", "is_completed": False},
            ]
        }

        out = to_toon_response("task.list", raw, request_input={"page": 2, "size": 1})
        self.assertEqual({"d": [{"n": "Task 2"}]}, out)

    def test_task_list_page_greater_than_one_returns_empty_when_no_more_items(self) -> None:
        raw = {
            "tasks": [
                {"id": f"t{index}", "content": f"Task {index}", "is_completed": False}
                for index in range(1, 51)
            ]
        }

        out = to_toon_response("task.list", raw, request_input={"page": 2, "size": 50})
        self.assertEqual({"d": []}, out)

    def test_task_list_preserves_next_cursor(self) -> None:
        raw = {
            "results": [{"id": "t1", "content": "Task 1", "is_completed": False}],
            "next_cursor": "cursor-1",
        }

        out = to_toon_response("task.list", raw)
        self.assertEqual({"d": [{"n": "Task 1"}], "next_cursor": "cursor-1"}, out)

    def test_task_projection_includes_section_reference(self) -> None:
        raw = {
            "id": "t1",
            "content": "Task 1",
            "section_id": "s1",
            "is_completed": False,
        }

        out = to_toon_response("task.get", raw)
        self.assertEqual({"d": {"n": "Task 1", "tg": "s1"}}, out)

    def test_task_list_by_date_filters_items_without_matching_due_date(self) -> None:
        raw = {
            "results": [
                {"id": "t1", "content": "Due date match", "due": {"date": "2026-02-18"}},
                {"id": "t2", "content": "No due"},
                {"id": "t3", "content": "Another date", "due": {"date": "2026-02-19"}},
            ]
        }

        out = to_toon_response("task.list_by_date", raw, request_input={"date": "2026-02-18"})
        self.assertEqual({"d": [{"n": "Due date match", "s": "2026-02-18"}]}, out)

    def test_task_list_today_filters_no_due_and_future_items(self) -> None:
        raw = {
            "results": [
                {"id": "t1", "content": "Overdue", "due": {"date": "2026-02-17"}},
                {"id": "t2", "content": "Today", "due": {"date": "2026-02-18"}},
                {"id": "t3", "content": "Future", "due": {"date": "2026-02-19"}},
                {"id": "t4", "content": "No due"},
            ]
        }

        out = to_toon_response("task.list_today", raw, request_input={"date": "2026-02-18"})
        self.assertEqual(
            {
                "d": [
                    {"n": "Overdue", "s": "2026-02-17"},
                    {"n": "Today", "s": "2026-02-18"},
                ]
            },
            out,
        )


if __name__ == "__main__":
    unittest.main()
