import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from todoist_proxy.methods import build_request, list_methods
from todoist_proxy.schemas import InputValidationError


class MethodsTests(unittest.TestCase):
    def test_build_request_for_every_method(self) -> None:
        cases = {
            "task.list": {
                "input": {"page": 1, "size": 10},
                "method": "GET",
                "path": "/tasks",
                "query": {},
                "body": {},
            },
            "task.list_by_project": {
                "input": {"project_id": "p1", "page": 2, "size": 50},
                "method": "GET",
                "path": "/tasks",
                "query": {"project_id": "p1"},
                "body": {},
            },
            "task.list_by_date": {
                "input": {"date": "2026-02-18", "page": 1, "size": 20},
                "method": "GET",
                "path": "/tasks",
                "query": {},
                "body": {},
            },
            "task.get": {
                "input": {"task_id": "t1"},
                "method": "GET",
                "path": "/tasks/t1",
                "query": {},
                "body": {},
            },
            "task.create": {
                "input": {
                    "name": "Task",
                    "description": "Desc",
                    "startDate": "2026-02-18T00:00:00Z",
                    "endDate": "2026-02-19T00:00:00Z",
                    "priority": "high",
                    "projectId": "p1",
                    "taskGroupId": "g1",
                    "labels": ["Работа"],
                },
                "method": "POST",
                "path": "/tasks",
                "query": {},
                "body": {
                    "name": "Task",
                    "description": "Desc",
                    "startDate": "2026-02-18T00:00:00Z",
                    "endDate": "2026-02-19T00:00:00Z",
                    "priority": "high",
                    "projectId": "p1",
                    "taskGroupId": "g1",
                    "labels": ["Работа"],
                },
            },
            "task.update": {
                "input": {"task_id": "t1", "name": "Task2"},
                "method": "POST",
                "path": "/tasks/t1",
                "query": {},
                "body": {"name": "Task2"},
            },
            "task.move": {
                "input": {"task_id": "t1", "projectId": "p2", "sectionId": "s2"},
                "method": "POST",
                "path": "/tasks/t1/move",
                "query": {},
                "body": {"projectId": "p2", "sectionId": "s2"},
            },
            "task.delete": {
                "input": {"task_id": "t1"},
                "method": "DELETE",
                "path": "/tasks/t1",
                "query": {},
                "body": {},
            },
            "task.close": {
                "input": {"task_id": "t1"},
                "method": "POST",
                "path": "/tasks/t1/close",
                "query": {},
                "body": {},
            },
            "project.list": {
                "input": {"page": 1, "size": 10},
                "method": "GET",
                "path": "/projects",
                "query": {},
                "body": {},
            },
            "project.get": {
                "input": {"project_id": "p1"},
                "method": "GET",
                "path": "/projects/p1",
                "query": {},
                "body": {},
            },
            "project.create": {
                "input": {"name": "P", "description": "D"},
                "method": "POST",
                "path": "/projects",
                "query": {},
                "body": {"name": "P", "description": "D"},
            },
            "project.update": {
                "input": {"project_id": "p1", "name": "P2"},
                "method": "POST",
                "path": "/projects/p1",
                "query": {},
                "body": {"name": "P2"},
            },
            "project.delete": {
                "input": {"project_id": "p1"},
                "method": "DELETE",
                "path": "/projects/p1",
                "query": {},
                "body": {},
            },
            "section.list_by_project": {
                "input": {"project_id": "p1", "page": 1, "size": 10},
                "method": "GET",
                "path": "/sections",
                "query": {"project_id": "p1"},
                "body": {},
            },
            "section.get": {
                "input": {"task_group_id": "g1"},
                "method": "GET",
                "path": "/sections/g1",
                "query": {},
                "body": {},
            },
            "section.create": {
                "input": {"name": "S", "description": "D", "projectId": "p1"},
                "method": "POST",
                "path": "/sections",
                "query": {},
                "body": {"name": "S", "description": "D", "projectId": "p1"},
            },
            "section.update": {
                "input": {"task_group_id": "g1", "name": "S2"},
                "method": "POST",
                "path": "/sections/g1",
                "query": {},
                "body": {"name": "S2"},
            },
            "section.delete": {
                "input": {"task_group_id": "g1"},
                "method": "DELETE",
                "path": "/sections/g1",
                "query": {},
                "body": {},
            },
            "checklist.create": {
                "input": {"task_id": "t1", "name": "C", "isCompleted": False},
                "method": "POST",
                "path": "/tasks",
                "query": {},
                "body": {"name": "C", "isCompleted": False},
            },
            "checklist.update": {
                "input": {"task_id": "t1", "checklist_item_id": "c1", "isCompleted": True},
                "method": "POST",
                "path": "/tasks/c1",
                "query": {},
                "body": {"isCompleted": True},
            },
            "checklist.delete": {
                "input": {"task_id": "t1", "checklist_item_id": "c1"},
                "method": "DELETE",
                "path": "/tasks/c1",
                "query": {},
                "body": {},
            },
        }

        self.assertEqual(set(cases), set(list_methods()))

        for method_name, expected in cases.items():
            with self.subTest(method_name=method_name):
                request = build_request(method_name, expected["input"])
                self.assertEqual(expected["method"], request.method)
                self.assertEqual(expected["path"], request.path)
                self.assertEqual(expected["query"], request.query)
                self.assertEqual(expected["body"], request.body)

    def test_unknown_method_fails(self) -> None:
        with self.assertRaises(InputValidationError):
            build_request("kanban.list", {})

    def test_missing_required_field_fails(self) -> None:
        with self.assertRaises(InputValidationError):
            build_request("task.get", {})

    def test_task_list_methods_forward_cursor_and_limit(self) -> None:
        task_list = build_request("task.list", {"cursor": "c1", "limit": 50})
        self.assertEqual({"cursor": "c1", "limit": 50}, task_list.query)

        list_by_project = build_request(
            "task.list_by_project",
            {"project_id": "p1", "cursor": "c2", "limit": 20},
        )
        self.assertEqual({"project_id": "p1", "cursor": "c2", "limit": 20}, list_by_project.query)

        list_by_date = build_request(
            "task.list_by_date",
            {"date": "2026-02-18", "cursor": "c3", "limit": 10},
        )
        self.assertEqual({"cursor": "c3", "limit": 10}, list_by_date.query)

    def test_task_create_and_update_accept_date_alias(self) -> None:
        create_request = build_request(
            "task.create",
            {"name": "Task", "description": "Desc", "date": "2026-02-18"},
        )
        self.assertEqual(
            {"name": "Task", "description": "Desc", "date": "2026-02-18"},
            create_request.body,
        )

        update_request = build_request(
            "task.update",
            {"task_id": "t1", "date": "2026-02-19"},
        )
        self.assertEqual({"date": "2026-02-19"}, update_request.body)

    def test_task_create_and_update_accept_section_aliases(self) -> None:
        create_request = build_request(
            "task.create",
            {"name": "Task", "section_id": "s1"},
        )
        self.assertEqual({"name": "Task", "section_id": "s1"}, create_request.body)

        update_request = build_request(
            "task.update",
            {"task_id": "t1", "sectionId": "s2"},
        )
        self.assertEqual({"sectionId": "s2"}, update_request.body)

    def test_task_create_and_update_accept_compact_priority_alias(self) -> None:
        create_request = build_request(
            "task.create",
            {"name": "Task", "p": 4},
        )
        self.assertEqual({"name": "Task", "p": 4}, create_request.body)

        update_request = build_request(
            "task.update",
            {"task_id": "t1", "p": 2},
        )
        self.assertEqual({"p": 2}, update_request.body)

    def test_task_create_and_update_accept_compact_labels_alias(self) -> None:
        create_request = build_request(
            "task.create",
            {"name": "Task", "l": ["Work"]},
        )
        self.assertEqual({"name": "Task", "l": ["Work"]}, create_request.body)

        update_request = build_request(
            "task.update",
            {"task_id": "t1", "l": ["Inbox", "Bug"]},
        )
        self.assertEqual({"l": ["Inbox", "Bug"]}, update_request.body)

    def test_task_move_accepts_section_aliases(self) -> None:
        move_request = build_request(
            "task.move",
            {"task_id": "t1", "sectionId": "s2"},
        )
        self.assertEqual({"sectionId": "s2"}, move_request.body)


if __name__ == "__main__":
    unittest.main()
