import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.service import execute_method


class RecordingClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, spec):
        self.calls.append(spec)
        if self.responses:
            return self.responses.pop(0)
        return {}


class ServiceTests(unittest.TestCase):
    def test_task_create_transforms_payload_to_todoist_fields(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        result = execute_method(
            client,
            "task.create",
            {
                "name": "Task",
                "description": "Desc",
                "startDate": "2026-02-18",
                "endDate": "2026-02-19T07:00:00Z",
                "priority": "high",
                "projectId": "p1",
                "taskGroupId": "s1",
                "labels": ["Работа"],
            },
        )

        self.assertEqual({"id": "t1"}, result)
        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual(
            {
                "content": "Task",
                "description": "Desc",
                "due_date": "2026-02-18",
                "deadline_date": "2026-02-19",
                "priority": 4,
                "project_id": "p1",
                "section_id": "s1",
                "labels": ["Работа"],
            },
            call.body,
        )

    def test_list_by_date_adds_filter_query(self) -> None:
        client = RecordingClient(responses=[[{"id": "t1"}]])

        execute_method(client, "task.list_by_date", {"date": "2026-02-18"})

        call = client.calls[0]
        self.assertEqual("GET", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual({"filter": "due on: 2026-02-18"}, call.query)

    def test_list_by_date_keeps_cursor_and_limit_query_params(self) -> None:
        client = RecordingClient(responses=[[{"id": "t1"}]])

        execute_method(
            client,
            "task.list_by_date",
            {"date": "2026-02-18", "cursor": "cursor-1", "limit": 25},
        )

        call = client.calls[0]
        self.assertEqual("GET", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual(
            {
                "filter": "due on: 2026-02-18",
                "cursor": "cursor-1",
                "limit": 25,
            },
            call.query,
        )

    def test_task_create_supports_date_alias_for_due_date(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.create",
            {
                "name": "Task",
                "description": "Desc",
                "date": "2026-02-18",
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual(
            {
                "content": "Task",
                "description": "Desc",
                "due_date": "2026-02-18",
            },
            call.body,
        )

    def test_checklist_create_with_completed_closes_created_subtask(self) -> None:
        client = RecordingClient(responses=[{"id": "c1", "content": "Sub"}, {}])

        result = execute_method(
            client,
            "checklist.create",
            {"task_id": "t1", "name": "Sub", "isCompleted": True},
        )

        self.assertEqual({"id": "c1", "content": "Sub"}, result)
        self.assertEqual(2, len(client.calls))
        self.assertEqual("POST", client.calls[0].method)
        self.assertEqual("/tasks", client.calls[0].path)
        self.assertEqual({"content": "Sub", "parent_id": "t1"}, client.calls[0].body)
        self.assertEqual("POST", client.calls[1].method)
        self.assertEqual("/tasks/c1/close", client.calls[1].path)

    def test_checklist_update_name_and_completion_then_gets_task(self) -> None:
        client = RecordingClient(responses=[{}, {}, {"id": "c1", "content": "Renamed", "is_completed": True}])

        result = execute_method(
            client,
            "checklist.update",
            {
                "task_id": "t1",
                "checklist_item_id": "c1",
                "name": "Renamed",
                "isCompleted": True,
            },
        )

        self.assertEqual({"id": "c1", "content": "Renamed", "is_completed": True}, result)
        self.assertEqual(3, len(client.calls))
        self.assertEqual("POST", client.calls[0].method)
        self.assertEqual("/tasks/c1", client.calls[0].path)
        self.assertEqual({"content": "Renamed"}, client.calls[0].body)
        self.assertEqual("POST", client.calls[1].method)
        self.assertEqual("/tasks/c1/close", client.calls[1].path)
        self.assertEqual("GET", client.calls[2].method)
        self.assertEqual("/tasks/c1", client.calls[2].path)


if __name__ == "__main__":
    unittest.main()
