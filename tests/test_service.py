import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.schemas import InputValidationError
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
                "priority": 3,
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

    def test_task_update_maps_section_alias_to_section_id(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.update",
            {
                "task_id": "t1",
                "sectionId": "s2",
                "name": "Task Updated",
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks/t1", call.path)
        self.assertEqual({"content": "Task Updated", "section_id": "s2"}, call.body)

    def test_task_update_accepts_compact_priority_alias(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.update",
            {
                "task_id": "t1",
                "p": 1,
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks/t1", call.path)
        self.assertEqual({"priority": 4}, call.body)

    def test_task_update_accepts_compact_labels_alias(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.update",
            {
                "task_id": "t1",
                "l": ["Work", "Urgent"],
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks/t1", call.path)
        self.assertEqual({"labels": ["Work", "Urgent"]}, call.body)

    def test_task_move_requires_target_project_or_section(self) -> None:
        client = RecordingClient(responses=[])

        with self.assertRaises(InputValidationError):
            execute_method(
                client,
                "task.move",
                {
                    "task_id": "t1",
                },
            )

        self.assertEqual([], client.calls)

    def test_task_move_calls_native_move_endpoint(self) -> None:
        client = RecordingClient(
            responses=[
                {
                    "id": "t1",
                    "content": "Task Source",
                    "project_id": "p2",
                    "section_id": "s2",
                }
            ]
        )

        result = execute_method(
            client,
            "task.move",
            {
                "task_id": "t1",
                "projectId": "p2",
                "sectionId": "s2",
            },
        )

        self.assertEqual({"id": "t1", "content": "Task Source", "project_id": "p2", "section_id": "s2"}, result)
        self.assertEqual(1, len(client.calls))
        self.assertEqual("POST", client.calls[0].method)
        self.assertEqual("/tasks/t1/move", client.calls[0].path)
        self.assertEqual(
            {
                "project_id": "p2",
                "section_id": "s2",
            },
            client.calls[0].body,
        )

    def test_task_move_to_project_without_section_sends_project_only(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.move",
            {
                "task_id": "t1",
                "projectId": "p2",
            },
        )

        self.assertEqual(
            {
                "project_id": "p2",
            },
            client.calls[0].body,
        )

    def test_task_move_with_section_alias_sends_section_only(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.move",
            {
                "task_id": "t1",
                "taskGroupId": "s2",
            },
        )

        self.assertEqual(
            {
                "section_id": "s2",
            },
            client.calls[0].body,
        )

    def test_task_create_accepts_p1_priority_notation(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.create",
            {
                "name": "Task",
                "priority": "P1",
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual({"content": "Task", "priority": 4}, call.body)

    def test_task_create_accepts_compact_labels_alias(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.create",
            {
                "name": "Task",
                "l": ["Work"],
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual({"content": "Task", "labels": ["Work"]}, call.body)

    def test_task_update_supports_explicit_empty_labels_for_clear(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.update",
            {
                "task_id": "t1",
                "labels": [],
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks/t1", call.path)
        self.assertEqual({"labels": []}, call.body)

    def test_task_create_supports_explicit_empty_labels(self) -> None:
        client = RecordingClient(responses=[{"id": "t1"}])

        execute_method(
            client,
            "task.create",
            {
                "name": "Task",
                "l": [],
            },
        )

        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks", call.path)
        self.assertEqual({"content": "Task", "labels": []}, call.body)

    def test_task_close_calls_close_endpoint(self) -> None:
        client = RecordingClient(responses=[{}])

        result = execute_method(client, "task.close", {"task_id": "t1"})

        self.assertEqual({}, result)
        call = client.calls[0]
        self.assertEqual("POST", call.method)
        self.assertEqual("/tasks/t1/close", call.path)
        self.assertEqual({}, call.body)

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
